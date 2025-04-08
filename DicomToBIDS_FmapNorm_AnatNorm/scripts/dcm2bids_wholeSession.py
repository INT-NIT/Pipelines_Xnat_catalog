import argparse
import collections
import json
import requests
import os
import glob
import sys
import subprocess
import time
import zipfile
import tempfile
import dicom as dicomLib
from shutil import copy as fileCopy
#from nipype.interfaces.dcm2nii import Dcm2nii
from collections import OrderedDict
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings()


def cleanServer(server):
    server.strip()
    if server[-1] == '/':
        server = server[:-1]
    if server.find('http') == -1:
        server = 'https://' + server
    return server


def isTrue(arg):
    return arg is not None and (arg == 'Y' or arg == '1' or arg == 'True')


def download(name, pathDict):
    if os.access(pathDict['absolutePath'], os.R_OK):
        try:
            os.symlink(pathDict['absolutePath'], name)
        except:
            fileCopy(pathDict['absolutePath'], name)
            print 'Copied %s.' % pathDict['absolutePath']
    else:
        with open(name, 'wb') as f:
            r = get(pathDict['URI'], stream=True)

            for block in r.iter_content(1024):
                if not block:
                    break

                f.write(block)
        print 'Downloaded file %s.' % name

def zipdir(dirPath=None, zipFilePath=None, includeDirInZip=True):
    if not zipFilePath:
        zipFilePath = dirPath + ".zip"
    if not os.path.isdir(dirPath):
        raise OSError("dirPath argument must point to a directory. "
            "'%s' does not." % dirPath)
    parentDir, dirToZip = os.path.split(dirPath)
    def trimPath(path):
        archivePath = path.replace(parentDir, "", 1)
        if parentDir:
            archivePath = archivePath.replace(os.path.sep, "", 1)
        if not includeDirInZip:
            archivePath = archivePath.replace(dirToZip + os.path.sep, "", 1)
        return os.path.normcase(archivePath)
    outFile = zipfile.ZipFile(zipFilePath, "w",
        compression=zipfile.ZIP_DEFLATED)
    for (archiveDirPath, dirNames, fileNames) in os.walk(dirPath):
        for fileName in fileNames:
            filePath = os.path.join(archiveDirPath, fileName)
            outFile.write(filePath, trimPath(filePath))
        # Make sure we get empty directories as well
        if not fileNames and not dirNames:
            zipInfo = zipfile.ZipInfo(trimPath(archiveDirPath) + "/")
            # some web sites suggest doing
            # zipInfo.external_attr = 16
            # or
            # zipInfo.external_attr = 48
            # Here to allow for inserting an empty directory.  Still TBD/TODO.
            outFile.writestr(zipInfo, "")
    outFile.close()


def check_dicom_header(name):
    fieldMapHeader = ""

    d = dicomLib.read_file(name)
    #print(d.keys())

    manufacturer_item = d.get_item((0x0008, 0x0070))

    if "SIEMENS" in manufacturer_item.value:
        print("Detected SIEMENS for Manufacturer (old version)")
        fieldMapHeader = d.get((0x0008, 0x0008), None)

    elif "Siemens Healthineers" in manufacturer_item.value:
        print("Detected Siemens Healthineers for Manufacturer (new version)")
        print(name)
        val = subprocess.check_output(["dcmdump", name], universal_newlines=True)
        #print(val)

        found_line = False
        for line in val.splitlines():
            #print(line)
            if "(0021,1175)" in line and not found_line:
                print("*** {}".format(line))
                if len(line.split("["))== 2:
                    line_left = line.split("[")[1]
                    print(line_left)
                    if len(line_left.split("]")) == 2:
                        line_right = line_left.split("]")[0]
                        print(line_right)
                        fieldMapHeader = line_right.split("\\")
                        found_line = True
        if not found_line:
            print("error, could not find (0021,1175) in val".format())

    else:
        print("Warning, Manufacturer = " + manufacturer_item.value + " is unknown, no fieldMapHeader")

    print("fieldMapHeader :", fieldMapHeader)

    return fieldMapHeader



BIDSVERSION = "1.0.1"

parser = argparse.ArgumentParser(description="Run dcm2niix on every file in a session")
parser.add_argument("--host", default="https://cnda.wustl.edu", help="CNDA host", required=True)
parser.add_argument("--user", help="CNDA username", required=True)
parser.add_argument("--password", help="Password", required=True)
parser.add_argument("--session", help="Session ID", required=True)
parser.add_argument("--subject", help="Subject Label", required=False)
parser.add_argument("--project", help="Project", required=False)
parser.add_argument("--dicomdir", help="Root output directory for DICOM files", required=True)
parser.add_argument("--niftidir", help="Root output directory for NIFTI files", required=True)
parser.add_argument("--overwrite", help="Overwrite NIFTI files if they exist")
parser.add_argument("--normFieldMap", help="Normalize FieldMap")
parser.add_argument("--normAnat", help="Normalize Anat")
parser.add_argument("--upload-by-ref", help="Upload \"by reference\". Only use if your host can read your file system.")
parser.add_argument("--workflowId", help="Pipeline workflow ID")
parser.add_argument('--version', action='version', version='%(prog)s 1')

args, unknown_args = parser.parse_known_args()
host = cleanServer(args.host)
session = args.session
subject = args.subject
project = args.project
overwrite = isTrue(args.overwrite)
dicomdir = args.dicomdir
niftidir = args.niftidir
workflowId = args.workflowId
uploadByRef = isTrue(args.upload_by_ref)

normFieldMap = isTrue(args.normFieldMap)
print("normFieldMap: ", normFieldMap)

normAnat = isTrue(args.normAnat)
print("normAnat: ", normAnat)

dcm2niixArgs = unknown_args if unknown_args is not None else []

### to do in DicomToBIDS

imgdir = niftidir + "/IMG"
bidsdir = niftidir + "/BIDS"

builddir = os.getcwd()

# Set up working directory
if not os.access(dicomdir, os.R_OK):
    print 'Making DICOM directory %s' % dicomdir
    os.mkdir(dicomdir)
if not os.access(niftidir, os.R_OK):
    print 'Making NIFTI directory %s' % niftidir
    os.mkdir(niftidir)
if not os.access(imgdir, os.R_OK):
    print 'Making NIFTI image directory %s' % imgdir
    os.mkdir(imgdir)
if not os.access(bidsdir, os.R_OK):
    print 'Making NIFTI BIDS directory %s' % bidsdir
    os.mkdir(bidsdir)

# Set up session
sess = requests.Session()
sess.verify = False
sess.auth = (args.user, args.password)


def get(url, **kwargs):
    try:
        r = sess.get(url, **kwargs)
        r.raise_for_status()
    except (requests.ConnectionError, requests.exceptions.RequestException) as e:
        print "Request Failed"
        print "    " + str(e)
        sys.exit(1)
    return r

if project is None or subject is None:
    # Get project ID and subject ID from session JSON
    print "Get project and subject ID for session ID %s." % session
    r = get(host + "/data/experiments/%s" % session, params={"format": "json", "handler": "values", "columns": "project,subject_ID"})
    sessionValuesJson = r.json()["ResultSet"]["Result"][0]
    project = sessionValuesJson["project"] if project is None else project
    subjectID = sessionValuesJson["subject_ID"]
    print "Project: " + project
    print "Subject ID: " + subjectID

    if subject is None:
        print
        print "Get subject label for subject ID %s." % subjectID
        r = get(host + "/data/subjects/%s" % subjectID, params={"format": "json", "handler": "values", "columns": "label"})
        subject = r.json()["ResultSet"]["Result"][0]["label"]
        print "Subject label: " + subject

# Get list of scan ids
print
print "Get scan list for session ID %s." % session
r = get(host + "/data/experiments/%s/scans" % session, params={"format": "json"})
scanRequestResultList = r.json()["ResultSet"]["Result"]

seriesDescList = []
scanIDList = []

for scan in scanRequestResultList:

    if 'series_description' in scan.keys():
        print('Founs series description to scan {}'.format(scan['ID']))
        seriesDescList.append(scan['series_description'])
        scanIDList.append(scan['ID'])

    elif 'type' in scan.keys():
        print('Fell back to scan type for {}'.format(scan['ID']))
        seriesDescList.append(scan['scan'])
        scanIDList.append(scan['ID'])
    else:
        print ("Warning, both series_description and type are missing for scan {}".format(scan['ID']))


print 'Found scans %s.' % ', '.join(scanIDList)

print 'Series descriptions/type %s' % ', '.join(seriesDescList)

## Fall back on scan type if series description field is empty
#if set(seriesDescList) == set(['']):
    #seriesDescList = [scan['type'] for scan in scanRequestResultList]
    #print 'Fell back to scan types %s' % ', '.join(seriesDescList)

# Get site- and project-level configs
bidsmaplist = []
print
print "Get site-wide BIDS map"
# We don't use the convenience get() method because that throws exceptions when the object is not found.
r = sess.get(host + "/data/config/bids/bidsmap", params={"contents": True})
print r
if r.ok:
    bidsmaptoadd = r.json()
    for mapentry in bidsmaptoadd:
        if mapentry not in bidsmaplist:
            bidsmaplist.append(mapentry)
else:
    print "Could not read site-wide BIDS map"

print "Get project BIDS map if one exists"
r = sess.get(host + "/data/projects/%s/config/bids/bidsmap" % project,
params={"contents": True})
print r.text
if r.ok:
    bidsmaptoadd = r.json()
    for mapentry in bidsmaptoadd:
        print mapentry
        if mapentry not in bidsmaplist:
            bidsmaplist.append(mapentry)
else:
    print "Could not read project BIDS map"

print "BIDS map: " + json.dumps(bidsmaplist)

# Collapse human-readable JSON to dict for processing
bidsnamemap = OrderedDict()

for x in bidsmaplist:
    if 'series_description' in x and 'bidsname' in x:
        bidsnamemap[x['series_description'].lower()] = x['bidsname']

# Map all series descriptions to BIDS names (case insensitive)
def any_match(target_string, bidsnamemap):
    for cur_key,cur_value in bidsnamemap.items():
        if cur_key in target_string:
            print("Found match {} in {}, return {}".format(cur_key, target_string, cur_value))
            return cur_value
    return False

#resolved = [bidsnamemap[x.lower()] for x in seriesDescList if x.lower() in
#bidsnamemap]

resolved = []
for x in seriesDescList:
    val = any_match(x.lower(), bidsnamemap)
    if val:
        resolved.append(val)


# Count occurrences
bidscount = collections.Counter(resolved)

# Remove multiples

#multiples = {seriesdesc: count for seriesdesc, count in bidscount.viewitems() if (count > 1 and not seriesdesc.startswith("task-"))} #previous version
#multiples = {}

#for seriesdesc, count in bidscount.viewitems():
    #if count > 1:
        #if seriesdesc.startswith("task-"):
            #continue

        #### NormFieldMap means only one will be kept
        #if seriesdesc.endswith("epi"):
            #continue

        ##if seriesdesc.endswith("dwi"):
            ##continue

        ##### NormAnat means only one will be kept
        #if seriesdesc.endswith("T1w"):
            #continue

        #if seriesdesc.endswith("T2w"):
            #continue

        #multiples[seriesdesc] = count

#print (multiples)

############### Checking is experiments has resources:

# Get scan resources
print "Get scan resources for scan %s." % session
r = get(host + "/data/experiments/%s/resources" % session, params={"format": "json"})
scanResources = r.json()["ResultSet"]["Result"]
print 'Found resources %s.' % ', '.join(res["label"] for res in scanResources)
print scanResources

# for task files and fieldmap files:
index_bmap = 0
store_previous_task_files = []

fmap_PA = False
fmap_AP = False

# Cheat and reverse scanid and seriesdesc lists so numbering is in the right order
for scanid, seriesdesc in zip(reversed(scanIDList), reversed(seriesDescList)):
    print
    print 'Beginning process for scan %s.' % scanid
    os.chdir(builddir)

    print 'Assigning BIDS name for scan %s.' % scanid

    # BIDS subject name
    if subject.startswith("sub-"):
        base = subject + "_"

    elif subject.startswith("sub_"):
        base = "sub-" + subject.split("_")[1] + "_"

    else:
        base = "sub-" + subject + "_"

    val = any_match(seriesdesc.lower(), bidsnamemap)

    if val is False:
        print "Series " + seriesdesc + " not found in BIDSMAP"
        # bidsname = "Z"
        continue  # Exclude series from processing

    print "Series " + seriesdesc + " matched " + val

    physio = False

    if seriesdesc.startswith("task-"):
        split_series = seriesdesc.split('_')
        if "PhysioLog" in split_series:
            print ("Found Physio")
            physio = True

        ### task should always end with bold if not PhysioLog
        elif split_series[-1] != "bold":

            # remove if full
            if split_series[-1] == "BOLD":
                split_series.pop()

            split_series.append("bold")

        match = "_".join(split_series)
    else:
        if val == "sbref":
            ### should not have sbref without task
            continue
        else:
            match = val


    if seriesdesc.startswith("ABCD"):
        print("Found ABCD session, skipping")
        continue

    # split before last _
    splitname = match.split("_")


    # special task
    # uncapitalize SBREF
    if splitname[-1] == "SBRef":
        splitname[-1] = "sbref"
    if  splitname[-1] == "bold":
        if splitname[-2] == "SBRef":
            splitname[-2] = "sbref"
            splitname = splitname[:-1]

    if any([atom == "bold" for atom in splitname[:-1]]):
        splitname.remove('bold')

    if "ME" in seriesdesc:
        assert len(splitname) > 2, "Error with {}".format(splitname)

        print(splitname)
        splitname.insert(-1, "echo-%e")
        print("After adding echo")
        print(splitname)

    print(splitname)
    match = "_".join(splitname)

    list_run = [atom.startswith("run") and len(atom.split("-"))!= 2 \
        for atom in splitname]

    print list_run
    if any(list_run):

        print("**** found error with run name: %s"%match)

        index = list_run.index(True)
        run_atom = splitname[index]
        print("run_atom: ", run_atom)

        run_index = run_atom.strip('run')

        print("run_index: ", run_index)
        assert run_index.isdigit(), "Error %s should be digit" %run_index

        splitname[index] = "run-%02d"%int(run_index)
        bidsname = "_".join(splitname)
        print ("**** final name :%s"%bidsname)

    else:
        bidsname = match

    # Get scan resources
    print "Get scan resources for scan %s." % scanid
    r = get(host + "/data/experiments/%s/scans/%s/resources" % (session, scanid), params={"format": "json"})
    scanResources = r.json()["ResultSet"]["Result"]
    print 'Found resources %s.' % ', '.join(res["label"] for res in scanResources)

    ##########
    # Do initial checks to determine if scan should be skipped
    hasNifti = any([res["label"] == "NIFTI" for res in scanResources])  # Store this for later
    if hasNifti and not overwrite:
        print "Scan %s has a preexisting NIFTI resource, and I am running with overwrite=False. Skipping." % scanid
        continue

    physioResourcesList = [res for res in scanResources if res['label'] == "secondary"]
    dicomResourceList = [res for res in scanResources if res["label"] == "DICOM"]
    imaResourceList = [res for res in scanResources if res["format"] == "IMA"]

    if physio:
        print physioResourcesList
        print dicomResourceList
        print imaResourceList

    elif len(dicomResourceList) == 0 and len(imaResourceList) == 0:
        print "Scan %s has no DICOM or IMA resource." % scanid
        # scanInfo['hasDicom'] = False
        continue
    elif len(dicomResourceList) == 0 and len(imaResourceList) > 1:
        print "Scan %s has more than one IMA resource and no DICOM resource. Skipping." % scanid
        # scanInfo['hasDicom'] = False
        continue
    elif len(dicomResourceList) > 1 and len(imaResourceList) == 0:
        print "Scan %s has more than one DICOM resource and no IMA resource. Skipping." % scanid
        # scanInfo['hasDicom'] = False
        continue
    elif len(dicomResourceList) > 1 and len(imaResourceList) > 1:
        print "Scan %s has more than one DICOM resource and more than one IMA resource. Skipping." % scanid
        # scanInfo['hasDicom'] = False
        continue

    dicomResource = dicomResourceList[0] if len(dicomResourceList) > 0 else None
    imaResource = imaResourceList[0] if len(imaResourceList) > 0 else None
    physioResource = physioResourcesList[0] if len(physioResourcesList) > 0 else None



    usingDicom = True if (len(dicomResourceList) == 1) else False

    if dicomResource is not None and dicomResource["file_count"]:
        if int(dicomResource["file_count"]) == 0:
            print "DICOM resource for scan %s has no files. Checking IMA resource." % scanid
            if imaResource["file_count"]:
                if int(imaResource["file_count"]) == 0:
                    print "IMA resource for scan %s has no files either. Skipping." % scanid
                    continue
            else:
                print "IMA resource for scan %s has a blank \"file_count\", so I cannot check it to see if there are no files. I am not skipping the scan, but this may lead to errors later if there are no files." % scanid
    elif imaResource is not None and imaResource["file_count"]:
        if int(imaResource["file_count"]) == 0:
            print "IMA resource for scan %s has no files. Skipping." % scanid
            continue
    elif  physioResource is not None and physioResource["file_count"]:
        if int(physioResource["file_count"]) == 0:
            print "Physio resource for scan %s has no files. Skipping." % scanid
            continue
    else:
        print "DICOM and IMA resources for scan %s both have a blank \"file_count\", so I cannot check to see if there are no files. I am not skipping the scan, but this may lead to errors later if there are no files." % scanid

    ##########
    # Prepare DICOM directory structure
    scanDicomDir = os.path.join(dicomdir, scanid)
    if not os.path.isdir(scanDicomDir):
        print 'Making scan DICOM directory %s.' % scanDicomDir
        os.mkdir(scanDicomDir)

    # Remove any existing files in the builddir.
    # This is unlikely to happen in any environment other than testing.
    for f in os.listdir(scanDicomDir):
        os.remove(os.path.join(scanDicomDir, f))


    ##########
    # Get list of DICOMs/IMAs

    # set resourceid. This will only be set if hasIma is true and we've found a resource id
    resourceid = None

    if not usingDicom:

        print 'Get IMA resource id for scan %s.' % scanid
        r = get(host + "/data/experiments/%s/scans/%s/resources" % (session, scanid), params={"format": "json"})
        resourceDict = {resource['format']: resource['xnat_abstractresource_id'] for resource in r.json()["ResultSet"]["Result"]}

        print(resourceDict)

        if "IMA" in resourceDict.keys():
            resourceid = resourceDict["IMA"]
        elif "DICOM" in resourceDict.keys():
            # case PhysioLog (DICOM but not "usingDicom")
            resourceid = resourceDict["DICOM"]
        else:
            print "Couldn't get xnat_abstractresource_id for IMA file list."

    # Deal with DICOMs
    print 'Get list of DICOM files for scan %s.' % scanid

    if usingDicom:
        filesURL = host + "/data/experiments/%s/scans/%s/resources/DICOM/files" % (session, scanid)
    elif resourceid is not None:
        filesURL = host + "/data/experiments/%s/scans/%s/resources/%s/files" % (session, scanid, resourceid)
    else:
        print "Trying to convert IMA files but there is no resource id available. Skipping."
        continue

    r = get(filesURL, params={"format": "json"})
    # I don't like the results being in a list, so I will build a dict keyed off file name
    dicomFileDict = {dicom['Name']: {'URI': host + dicom['URI']} for dicom in r.json()["ResultSet"]["Result"]}

    # Have to manually add absolutePath with a separate request
    r = get(filesURL, params={"format": "json", "locator": "absolutePath"})
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

    (name, pathDict) = dicomFileList[0]
    download(name, pathDict)


    if usingDicom:
        print 'Checking modality in DICOM headers of file %s.' % name
        d = dicomLib.read_file(name)
        modalityHeader = d.get((0x0008, 0x0060), None)
        if modalityHeader:
            print 'Modality header: %s' % modalityHeader
            modality = modalityHeader.value.strip("'").strip('"')
            if modality == 'SC' or modality == 'SR':
                print 'Scan %s is a secondary capture. Skipping.' % scanid
                continue
        else:
            print 'Could not read modality from DICOM headers. Skipping.'
            continue


    ############################################## special case of fieldmap

    temp_delete = False

    if "epi" in splitname and usingDicom:
        print '****** Checking NORM (fieldMap) in DICOM headers of file %s.' % name

        fieldMapHeader = check_dicom_header(name)

        print(fieldMapHeader)

        if "NORM" in fieldMapHeader and not normFieldMap:
            print("***** Norm found but not expected, skipping...")
            temp_delete= True
        elif not "NORM" in fieldMapHeader and normFieldMap:
            print("***** Norm not found but expected, skipping...")
            temp_delete=True
        else:
            print("***** Found corresponding normFieldMap")

    if temp_delete:
        continue

    ################################################ Now case of AnatNorm

    temp_delete = False

    if ("T1w" in splitname or "T2w" in splitname) and usingDicom:
        print '****** Checking NORM (Anat) in DICOM headers of file %s.' % name

        AnatHeader = check_dicom_header(name)
        print(AnatHeader)

        if "NORM" in AnatHeader and not normAnat:
            print("***** Norm found but not expected, skipping...")
            temp_delete= True
        elif not "NORM" in AnatHeader and normAnat:
            print("***** Norm not found but expected, skipping...")
            temp_delete=True
        else:
            print("***** Found corresponding normFieldMap")

    if temp_delete:
        continue



    ##########
    # Download remaining DICOMs
    for name, pathDict in dicomFileList[1:]:
        download(name, pathDict)

    os.chdir(builddir)
    print 'Done downloading for scan %s.' % scanid
    print

    ##########
    # Prepare NIFTI directory structure
    scanBidsDir = os.path.join(bidsdir, scanid)
    if not os.path.isdir(scanBidsDir):
        print 'Creating scan NIFTI BIDS directory %s.' % scanBidsDir
        os.mkdir(scanBidsDir)

    if not physio:
        scanImgDir = os.path.join(imgdir, scanid)
        if not os.path.isdir(scanImgDir):
            print 'Creating scan NIFTI image directory %s.' % scanImgDir
            os.mkdir(scanImgDir)

        for f in os.listdir(scanImgDir):
            os.remove(os.path.join(scanImgDir, f))


    # Remove any existing files in the builddir.
    # This is unlikely to happen in any environment other than testing.
    for f in os.listdir(scanBidsDir):
        os.remove(os.path.join(scanBidsDir, f))

    # Convert the differences

    bidsname = base + bidsname
    print "Base " + base + " series " + seriesdesc + " match " + bidsname

    print 'Converting scan %s to NIFTI...' % scanid
    # Do some stuff to execute dcm2niix as a subprocess

    # if PhysioLog, do not convert, just rename
    if  physio:

        print (bidsname)

        file_dcm  = [f for f in os.listdir(scanDicomDir)]
        print file_dcm
        os.rename(os.path.join(scanDicomDir, f), os.path.join(scanBidsDir, bidsname + ".dcm"))

        print("Done renaming Physio")

    else:
        if usingDicom :
            dcm2niix_command = "dcm2niix -b y -z y".split() + dcm2niixArgs + " -f {} -o {} {}".format(bidsname, scanBidsDir, scanDicomDir).split()
            print "Executing command: " + " ".join(dcm2niix_command)
            print subprocess.check_output(dcm2niix_command)

            ### checking if bidsname have been modified:

            print("TMP: scanBidsDir= ", scanBidsDir)
            print("TMP:  os.listdir(scanBidsDir)= ",  os.listdir(scanBidsDir))

            list_file_size = []
            list_file = []

            for f in os.listdir(scanBidsDir):

                print("TMP: f= ", f)

                point_split = f.split(".")

                file_name = point_split[0]
                print(file_name)

                extension = ".".join(point_split[1:])

                print(extension)

                if "echo" not in file_name:
                    if extension != "json":
                        list_file_size.append(
                            os.path.getsize(os.path.join(scanBidsDir, f)))

                        list_file.append(file_name)

                    #if file_name != bidsname :
                        #print "Renaming file {} to {}".format(f, bidsname+"."+extension)
                        #os.rename(os.path.join(scanBidsDir,f), os.path.join(scanBidsDir, bidsname+"."+extension))
                else:
                    print("found echo in {}, skipping rename".format(file_name))
                    print(os.listdir(scanBidsDir))
                    print(bidsname+"."+extension)

            print(list_file)
            print(list_file_size)

            if len(list_file_size) == 2:

                order = sorted(range(len(list_file_size)),
                               key=lambda k: list_file_size[k])

                #order = [i[0] for i in sorted(enumerate(list_file_size), key=lambda x:x[1])]

                print("Order:")
                print(order)

                print("First:")

                print(list_file[order[1]])
                print(list_file_size[order[1]])

                print(os.path.join(
                        scanBidsDir, list_file[order[1]] + ".nii.gz"))

                print(os.path.join(
                        scanBidsDir, list_file[order[1]] + ".json"))

                print("Second:")

                print(list_file[order[0]])
                print(list_file_size[order[0]])

                assert os.path.exists(
                    os.path.join(
                        scanBidsDir, list_file[order[0]] + ".nii.gz")), \
                    "Error with {}".format(list_file[order[0]] + ".nii.gz")

                os.remove(
                    os.path.join(scanBidsDir,
                                 list_file[order[1]] + ".nii.gz"))

                assert os.path.exists(
                    os.path.join(
                        scanBidsDir, list_file[order[0]] + ".json")), \
                    "Error with {}".format(list_file[order[0]] + ".json")

                os.remove(
                    os.path.join(scanBidsDir,
                                 list_file[order[1]] + ".json"))

                if list_file[order[1]].endswith("bolda"):
                    print("Renaming {}".format(list_file[order[1]]))

                    os.rename(
                        os.path.join(
                            scanBidsDir, list_file[order[1]] + ".nii.gz"),
                        os.path.join(
                            scanBidsDir, list_file[order[0]] + ".nii.gz"))

                    os.rename(
                        os.path.join(
                            scanBidsDir, list_file[order[1]] + ".json"),
                        os.path.join(
                            scanBidsDir, list_file[order[0]] + ".json"))

            elif len(list_file_size) >2:
                print("***** Error, not for echo, skipping")
                continue

            # Modify json if task-

            print(bidsname)
            print("ME" in bidsname)

            if "ME" in bidsname:
                print("ME found, skipping")

            else:

                list_task = [atom.startswith("task") and len(atom.split("-")) == 2
                    and not atom.endswith("ME") for atom in splitname]

                print(list_task)

                if any(list_task):

                    task = splitname[list_task.index(True)].split("-")[1]

                    print("**** found task- with run name: {}".format(task))

                    json_bids_file = os.path.join(scanBidsDir,
                                                  bidsname)+".json"

                    if "bold" in splitname:
                        new_json_contents = {
                            'TaskName': task,
                            'B0FieldSource': "B0map" + str(index_bmap)}
                    else:
                        new_json_contents = {
                            'TaskName': task}

                    with open(json_bids_file) as f:
                        data = json.load(f)

                    data.update(new_json_contents)

                    with open(json_bids_file, 'w') as f:
                        json.dump(data, f)

                    if "bold" in splitname:

                        print("Found bold, preparing IntendedFor with subject " + subject)
                        split_ses = subject.split("_")
                        print(split_ses)
                        if len(split_ses) >= 2:
                            print(split_ses[1])
                            if split_ses[1].startswith("ses-"):
                                session_id = split_ses[1]

                                nii_bids_file = os.path.join(
                                    session_id, "func",
                                    bidsname+".nii.gz")
                            else:

                                nii_bids_file = os.path.join(
                                    "func",
                                    bidsname+".nii.gz")

                        else:

                            nii_bids_file = os.path.join(
                                "func",
                                bidsname+".nii.gz")

                        print(nii_bids_file)

                        store_previous_task_files.append(nii_bids_file)

                # Modify json if epi
                if "epi" in bidsname:
                    print("Modifying json for epi")

                    json_bids_file = os.path.join(scanBidsDir, bidsname)+".json"

                    if 'dir-PA' in bidsname:
                        print("fmap PA found")
                        fmap_PA = True
                    elif 'dir-AP' in bidsname:
                        print("fmap AP found")
                        fmap_AP = True

                    json_bids_file = os.path.join(scanBidsDir, bidsname)+".json"

                    new_json_contents = {
                        'B0FieldIdentifier': "B0map" + str(index_bmap),
                        "IntendedFor": store_previous_task_files}

                    with open(json_bids_file) as f:
                        data = json.load(f)

                    data.update(new_json_contents)

                    with open(json_bids_file, 'w') as f:
                        json.dump(data, f)

                    if fmap_PA and fmap_AP:
                        fmap_PA = False
                        fmap_AP = False
                        index_bmap = index_bmap+1
                        store_previous_task_files = []

        else:
            # call dcm2nii for converting ima files
            print subprocess.check_output("dcm2nii -b @PIPELINE_DIR_PATH@/catalog/DicomToBIDS/resources/dcm2nii.ini -g y -f Y -e N -p N -d N -o {} {}".format(scanBidsDir, scanDicomDir).split())

            #print subprocess.check_output("mv {}/*.nii.gz {}/{}.nii.gz".format(scanBidsDir, scanBidsDir, "bidsname").split())

            # there should only be one file in this folder
            for files in glob.glob(os.path.join(scanBidsDir, "*.nii.gz")):
                os.rename(files, os.path.join(scanBidsDir, bidsname + ".nii.gz"))

            # Create BIDS sidecar file from IMA XML
            imaSessionURL = host + "/data/archive/experiments/%s/scans/%s" % (session, scanid)
            r = get(imaSessionURL, params={"format": "json"})

            # fields from ima json result
            imaResultChildren = r.json()["items"][0]["children"][1]["items"][0]["data_fields"]
            imaResultDataFields = r.json()["items"][0]["data_fields"]

            #fileDimX = imaResultChildren["dimensions/x"] if "dimensions/x" in imaResultChildren else None
            #fileDimY = imaResultChildren["dimensions/y"] if "dimensions/y" in imaResultChildren else None
            #fileDimZ = imaResultChildren["dimensions/z"] if "dimensions/z" in imaResultChildren else None
            #fileDimVolumes = imaResultChildren["dimensions/volumes"] if "dimensions/volumes" in imaResultChildren else None
            #fileVoxelResX = imaResultChildren["voxelRes/x"] if "voxelRes/x" in imaResultChildren else None
            #fileVoxelResY = imaResultChildren["voxelRes/y"] if "voxelRes/y" in imaResultChildren else None
            #fileVoxelResZ = imaResultChildren["voxelRes/z"] if "voxelRes/z" in imaResultChildren else None
            #fileVoxelResUnits = imaResultChildren["voxelRes/units"] if "voxelRes/units" in imaResultChildren else None
            #fileOrientation = imaResultChildren["orientation"] if "orientation" in imaResultChildren else None
            #parametersFovX = imaResultDataFields["parameters/fov/x"] if "parameters/fov/x" in imaResultDataFields else None
            #parametersFovY = imaResultDataFields["parameters/fov/y"] if "parameters/fov/y" in imaResultDataFields else None
            #parametersMatrixX = imaResultDataFields["parameters/matrix/x"] if "parameters/matrix/x" in imaResultDataFields else None
            #parametersMatrixY = imaResultDataFields["parameters/matrix/y"] if "parameters/matrix/y" in imaResultDataFields else None
            parametersTr = imaResultDataFields["parameters/tr"] if "parameters/tr" in imaResultDataFields else None
            parametersTe = imaResultDataFields["parameters/te"] if "parameters/te" in imaResultDataFields else None
            parametersFlip = imaResultDataFields["parameters/flip"] if "parameters/flip" in imaResultDataFields else None
            #parametersSequence = imaResultDataFields["parameters/sequence"] if "parameters/sequence" in imaResultDataFields else None
            #parametersOrigin = imaResultDataFields["parameters/origin"] if "parameters/origin" in imaResultDataFields else None

            # Manually added data
            scannerManufacturer = "Siemens"
            scannerManufacturerModelName = "Vision"
            scannerMagneticFieldStrength = 1.5
            conversionSoftware = "dcm2nii"
            conversionSoftwareVersion = "2013.06.12"

            # create a BIDS sidecar json file from the data we got
            json_contents = {}

            # scanner info
            json_contents['Manufacturer'] = scannerManufacturer
            json_contents['ManufacturersModelName'] = scannerManufacturerModelName
            json_contents['MagneticFieldStrength'] = scannerMagneticFieldStrength

            # scan-specific info
            #json_contents['AcquisitionTime'] = ""
            #json_contents['SeriesNumber'] = ""
            json_contents['EchoTime'] = parametersTe
            json_contents['RepetitionTime'] = parametersTr
            json_contents['FlipAngle'] = parametersFlip

            json_contents['ConversionSoftware'] = conversionSoftware
            json_contents['ConversionSoftwareVersion'] = conversionSoftwareVersion

            # output BIDS sidecar file, make sure the name is the same as the .nii.gz output filename

            # get base of bidsname (get name from name.nii.gz) to construct json filename
            with open(os.path.join(scanBidsDir, bidsname) + ".json", "w+") as outfile:
                json.dump(json_contents, outfile, indent=4)

        print 'Done dcm2niix.'

        if not physio:
            # Move imaging to image directory
            for f in os.listdir(scanBidsDir):

                if "nii" in f:
                    print("Renaming {} to {}".format(
                        os.path.join(scanBidsDir, f),
                        os.path.join(scanImgDir, f)))

                    os.rename(os.path.join(scanBidsDir, f), os.path.join(scanImgDir, f))

    ##########
    # Upload results
    print
    print 'Preparing to upload files for scan %s.' % scanid

    # If we have a NIFTI resource and we've reached this point, we know overwrite=True.
    # We should delete the existing NIFTI resource.
    if hasNifti:
        print "Scan %s has a preexisting NIFTI resource. Deleting it now." % scanid

        try:
            queryArgs = {}
            if workflowId is not None:
                queryArgs["event_id"] = workflowId
            r = sess.delete(host + "/data/experiments/%s/scans/%s/resources/NIFTI" % (session, scanid), params=queryArgs)
            r.raise_for_status()

            r = sess.delete(host + "/data/experiments/%s/scans/%s/resources/BIDS" % (session, scanid), params=queryArgs)
            r.raise_for_status()
        except (requests.ConnectionError, requests.exceptions.RequestException) as e:
            print "There was a problem deleting"
            print "    " + str(e)
            print "Skipping upload for scan %s." % scanid
            continue

    # Uploading
    if not physio:
        print 'Uploading files for scan %s' % scanid
        queryArgs = {"format": "NIFTI", "content": "NIFTI_RAW", "tags": "BIDS"}
        if workflowId is not None:
            queryArgs["event_id"] = workflowId
        if uploadByRef:
            queryArgs["reference"] = os.path.abspath(scanImgDir)
            r = sess.put(host + "/data/experiments/%s/scans/%s/resources/NIFTI/files" % (session, scanid), params=queryArgs)
        else:
            queryArgs["extract"] = True
            (t, tempFilePath) = tempfile.mkstemp(suffix='.zip')
            zipdir(dirPath=os.path.abspath(scanImgDir), zipFilePath=tempFilePath, includeDirInZip=False)
            files = {'file': open(tempFilePath, 'rb')}
            r = sess.put(host + "/data/experiments/%s/scans/%s/resources/NIFTI/files" % (session, scanid), params=queryArgs, files=files)
            os.remove(tempFilePath)
        r.raise_for_status()


    queryArgs = {"format": "BIDS", "content": "BIDS", "tags": "BIDS"}
    if workflowId is not None:
        queryArgs["event_id"] = workflowId
    if uploadByRef:
        queryArgs["reference"] = os.path.abspath(scanBidsDir)
        r = sess.put(host + "/data/experiments/%s/scans/%s/resources/BIDS/files" % (session, scanid), params=queryArgs)
    else:
        queryArgs["extract"] = True
        (t, tempFilePath) = tempfile.mkstemp(suffix='.zip')
        zipdir(dirPath=os.path.abspath(scanBidsDir), zipFilePath=tempFilePath, includeDirInZip=False)
        files = {'file': open(tempFilePath, 'rb')}
        r = sess.put(host + "/data/experiments/%s/scans/%s/resources/BIDS/files" % (session, scanid), params=queryArgs, files=files)
        os.remove(tempFilePath)
    r.raise_for_status()

    ##########
    # Clean up input directory
    print
    print 'Cleaning up %s directory.' % scanDicomDir
    for f in os.listdir(scanDicomDir):
        os.remove(os.path.join(scanDicomDir, f))
    os.rmdir(scanDicomDir)

    print
    print 'All done with image conversion.'

##########
# Generate session-level metadata files
previouschanges = ""

# Remove existing files if they are there
print "Check for presence of session-level BIDS data"
r = get(host + "/data/experiments/%s/resources" % session, params={"format": "json"})
sessionResources = r.json()["ResultSet"]["Result"]
print 'Found resources %s.' % ', '.join(res["label"] for res in sessionResources)

# Do initial checks to determine if session-level BIDS metadata is present
hasSessionBIDS = any([res["label"] == "BIDS" for res in sessionResources])

if hasSessionBIDS:
    print "Session has preexisting BIDS resource. Deleting previous BIDS metadata if present."

    # Consider making CHANGES a real, living changelog
    # r = get( host + "/data/experiments/%s/resources/BIDS/files/CHANGES"%(session) )
    # previouschanges = r.text
    # print previouschanges

    try:
        queryArgs = {}
        if workflowId is not None:
            queryArgs["event_id"] = workflowId

        r = sess.delete(host + "/data/experiments/%s/resources/BIDS" % session, params=queryArgs)
        r.raise_for_status()
        uploadSessionBids = True
    except (requests.ConnectionError, requests.exceptions.RequestException) as e:
        print "There was a problem deleting"
        print "    " + str(e)
        print "Skipping upload for session-level files."
        uploadSessionBids = False

    print "Done"
    print ""

# Fetch metadata from project
print "Fetching project {} metadata".format(project)
rawprojectdata = get(host + "/data/projects/%s" % project, params={"format": "json"})
projectdata = rawprojectdata.json()
print "Got project metadata\n"

# Build dataset description
print "Constructing BIDS data"
dataset_description = OrderedDict()
dataset_description['Name'] = project

dataset_description['BIDSVersion'] = BIDSVERSION

# License- to be added later on after discussion of sensible default options
# dataset_description['License'] = None

# Compile investigators and PI into names list
invnames = []
invfield = [x for x in projectdata["items"][0]["children"] if x["field"] == "investigators/investigator"]
print str(invfield)

if invfield != []:
    invs = invfield[0]["items"]

    for i in invs:
        invnames.append(" ".join([i["data_fields"]["firstname"], i["data_fields"]["lastname"]]))

pifield = [x for x in projectdata["items"][0]["children"] if x["field"] == "PI"]

if pifield != []:
    pi = pifield[0]["items"][0]["data_fields"]
    piname = " ".join([pi["firstname"], pi["lastname"]])

    if piname in invnames:
        invnames.remove(piname)

    invnames.insert(0, piname + " (PI)")

if invnames != []:
    dataset_description['Authors'] = invnames

# Other metadata - to be added later on
# dataset_description['Acknowledgments'] = None
# dataset_description['HowToAcknowledge'] = None
# dataset_description['Funding'] = None
# dataset_description['ReferencesAndLinks'] = None

# Session identifier
dataset_description['DatasetDOI'] = host + '/data/experiments/' + session

# Upload
queryArgs = {"format": "BIDS", "content": "BIDS", "tags": "BIDS", "inbody": "true"}
if workflowId is not None:
    queryArgs["event_id"] = workflowId

r = sess.put(host + "/data/experiments/%s/resources/BIDS/files/dataset_description.json" % session, json=dataset_description, params=queryArgs)
r.raise_for_status()

# Generate CHANGES
changes = "1.0 " + time.strftime("%Y-%m-%d") + "\n\n - Initial release."

# Upload
h = {"content-type": "text/plain"}
r = sess.put(host + "/data/experiments/%s/resources/BIDS/files/CHANGES" % session, data=changes, params=queryArgs, headers=h)
r.raise_for_status()

# All done
print 'All done with session-level metadata.'
