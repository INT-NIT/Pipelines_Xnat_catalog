import argparse, requests, os, sys, subprocess
import dicom as dicomLib
from shutil import copy as fileCopy
#from nipype.interfaces.dcm2nii import Dcm2nii

def cleanServer(server):
    server.strip()
    if (server[-1] == '/'):
        server = server[:-1]
    if (server.find('http') == -1):
        server = 'https://' + server
    return server

def isTrue(arg):
    return arg is not None and (arg=='Y' or arg=='1' or arg=='True')

def download(name,pathDict):
    if os.access(pathDict['absolutePath'], os.R_OK):
        try:
            os.symlink(pathDict['absolutePath'],name)
            print 'Made link to %s.' % pathDict['absolutePath']
        except:
            fileCopy(pathDict['absolutePath'],name)
            print 'Copied %s.' % pathDict['absolutePath']
    else:
        with open(name, 'wb') as f:
            r = get(pathDict['URI'], stream=True)

            for block in r.iter_content(1024):
                if not block:
                    break

                f.write(block)
        print 'Downloaded file %s.' % name



parser = argparse.ArgumentParser(description="Run dcm2nii on every file in a session")
parser.add_argument("--host", default="https://cnda.wustl.edu", help="CNDA host", required=True)
parser.add_argument("--user", help="CNDA username", required=True)
parser.add_argument("--password", help="Password", required=True)
parser.add_argument("--session", help="Session ID", required=True)
parser.add_argument("--dicomdir", help="Root output directory for DICOM files", required=True)
parser.add_argument("--niftidir", help="Root output directory for NIFTI files", required=True)
parser.add_argument("--overwrite", help="Overwrite NIFTI files if they exist")
parser.add_argument("--workflowId", help="Pipeline workflow ID")
parser.add_argument('--version', action='version', version='%(prog)s 1')

args = parser.parse_args()
host = cleanServer(args.host)
session = args.session
overwrite = isTrue(args.overwrite)
dicomdir = args.dicomdir
niftidir = args.niftidir
workflowId = args.workflowId

builddir = os.getcwd()

# Set up working directory
if not os.access(dicomdir, os.R_OK):
    print 'Making DICOM directory %s' % dicomdir
    os.mkdir(dicomdir)
if not os.access(niftidir, os.R_OK):
    print 'Making NIFTI directory %s' % niftidir
    os.mkdir(niftidir)


# Set up session
sess = requests.Session()
sess.verify = False
sess.auth = (args.user,args.password)

def get(url,**kwargs):
    try:
        r = sess.get( url, **kwargs )
        r.raise_for_status()
    except (requests.ConnectionError, requests.exceptions.RequestException) as e:
        print "Request Failed"
        print "    " + str( e )
        sys.exit(1)
    return r

# Get list of scan ids
print "Get scan list for session ID %s." % session
r = get( host+"/data/experiments/%s/scans?format=json"%session )
scanRequestResultList = r.json()["ResultSet"]["Result"]
scanIDList = [scan['ID'] for scan in scanRequestResultList]
print 'Found scans %s.'%', '.join(scanIDList)

for scanid in scanIDList:
    print
    print 'Beginning process for scan %s.'%scanid
    os.chdir(builddir)

    # Get scan resources
    print "Get scan resources for scan %s." % scanid
    r = get( host+"/data/experiments/%s/scans/%s/resources?format=json"%(session,scanid) )
    scanResources = r.json()["ResultSet"]["Result"]
    print 'Found resources %s.'%', '.join(res["label"] for res in scanResources)

    ##########
    # Do initial checks to determine if scan should be skipped
    hasNifti = any([res["label"]=="NIFTI" for res in scanResources]) # Store this for later
    if hasNifti and not overwrite:
        print "Scan %s has a preexisting NIFTI resource, and I am running with overwrite=False. Skipping." % scanid
        continue

    dicomResourceList = [res for res in scanResources if res["label"]=="DICOM"]
    if len(dicomResourceList) == 0:
        print "Scan %s has no DICOM resource. Skipping." % scanid
        # scanInfo['hasDicom'] = False
        continue
    elif len(dicomResourceList) > 1:
        print "Scan %s has more than one DICOM resource. Skipping." % scanid
        # scanInfo['hasDicom'] = False
        continue

    dicomResource = dicomResourceList[0]
    if int(dicomResource["file_count"]) == 0:
        print "DICOM resource for scan %s has no files. Skipping." % scanid
        # scanInfo['hasDicom'] = True
        continue

    ##########
    # Prepare DICOM directory structure
    print
    scanDicomDir = os.path.join(dicomdir,scanid)
    if not os.path.isdir(scanDicomDir):
        print 'Making scan DICOM directory %s.' % scanDicomDir
        os.mkdir(scanDicomDir)
    # Remove any existing files in the builddir.
    # This is unlikely to happen in any environment other than testing.
    for f in os.listdir(scanDicomDir):
        os.remove(os.path.join(scanDicomDir,f))

    ##########
    # Get list of DICOMs
    print 'Get list of DICOM files for scan %s.' % scanid
    r = get( host+"/data/experiments/%s/scans/%s/resources/DICOM/files?format=json"%(session,scanid) )
    # I don't like the results being in a list, so I will build a dict keyed off file name
    dicomFileDict = {dicom['Name']: {'URI':dicom['URI']} for dicom in r.json()["ResultSet"]["Result"]}

    # Have to manually add absolutePath with a separate request
    r = get( host+"/data/experiments/%s/scans/%s/resources/DICOM/files?format=json&locator=absolutePath"%(session,scanid) )
    for dicom in r.json()["ResultSet"]["Result"]:
        dicomFileDict[dicom['Name']]['absolutePath'] = dicom['absolutePath']

    ##########
    # Download DICOMs
    print "Downloading files for scan %s." % scanid
    os.chdir(scanDicomDir)

    # Check secondary
    # Download any one DICOM from the series and check its headers
    # If the headers indicate it is a secondary capture, we will skip this series.
    dicomFileList = dicomFileDict.items()

    (name,pathDict) = dicomFileList[0]
    download(name,pathDict)

    print 'Checking modality in DICOM headers of file %s.'%name
    d = dicomLib.read_file(name)
    modalityHeader = d.get((0x0008,0x0060), None)
    if modalityHeader:
        print 'Modality header: %s'%modalityHeader
        modality = modalityHeader.value.strip("'").strip('"')
        if modality == 'SC' or modality == 'SR':
            print 'Scan %s is a secondary capture. Skipping.' % scanid
            continue
    else:
        print 'Could not read modality from DICOM headers. Skipping.'
        continue

    ##########
    # Download remaining DICOMs
    for name,pathDict in dicomFileList[1:]:
        download(name,pathDict)

    os.chdir(builddir)
    print 'Done downloading for scan %s.'%scanid
    print


    ##########
    # Prepare NIFTI directory structure
    scanNiftiDir = os.path.join(niftidir,scanid)
    if not os.path.isdir(scanNiftiDir):
        print 'Creating scan NIFTI directory %s.' % scanNiftiDir
        os.mkdir(scanNiftiDir)
    # Remove any existing files in the builddir.
    # This is unlikely to happen in any environment other than testing.
    for f in os.listdir(scanNiftiDir):
        os.remove(os.path.join(scanNiftiDir,f))


    print 'Converting scan %s to NIFTI...' % scanid
    # Do some stuff to execute dcm2niix as a subprocess
    print subprocess.check_output("dcm2niix -z n -f %i_%p_%s -o {} {}".format(scanNiftiDir, scanDicomDir).split())
    ## Special G. Auzias
    #print subprocess.check_output("dcm2niix -i y -z i -f %p_%s -o {} {}".format(scanNiftiDir, scanDicomDir).split())
    print 'Done.'

    ##########
    # Upload results
    print
    print 'Preparing to upload files for scan %s.'%scanid

    # If we have a NIFTI resource and we've reached this point, we know overwrite=True.
    # We should delete the existing NIFTI resource.
    if hasNifti:
        print "Scan %s has a preexisting NIFTI resource. Deleting it now." % scanid
        try:
            queryArgs = {}
            if workflowId is not None:
                queryArgs["event_id"] = workflowId
            r = sess.delete( host+"/data/experiments/%s/scans/%s/resources/NIFTI"%(session,scanid), params=queryArgs )
            r.raise_for_status()
        except (requests.ConnectionError, requests.exceptions.RequestException) as e:
            print "There was a problem deleting"
            print "    " + str( e )
            print "Skipping upload for scan %s." %scanid
            continue

    # Uploading
    print 'Uploading files for scan %s' % scanid
    queryArgs = {"format":"NIFTI","content":"NIFTI_RAW","reference":os.path.abspath(scanNiftiDir)}
    if workflowId is not None:
        queryArgs["event_id"] = workflowId
    r = sess.put( host+"/data/experiments/%s/scans/%s/resources/NIFTI/files" % (session,scanid), params=queryArgs )
    r.raise_for_status()

    ##########
    # Clean up input directory
    print
    print 'Cleaning up %s directory.'%scanDicomDir
    for f in os.listdir(scanDicomDir):
        os.remove(os.path.join(scanDicomDir,f))
    os.rmdir(scanDicomDir)


print
print 'All done.'
