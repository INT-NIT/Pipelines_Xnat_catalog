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

parser = argparse.ArgumentParser(description="Run dcm2niix on every file in a session")
parser.add_argument("--host", default="https://cnda.wustl.edu", help="CNDA host", required=True)
parser.add_argument("--user", help="CNDA username", required=True)
parser.add_argument("--password", help="Password", required=True)
parser.add_argument("--session", help="Session ID", required=True)
parser.add_argument("--subject", help="Subject Label", required=False)
parser.add_argument("--project", help="Project", required=False)
parser.add_argument("--topupdir", help="Root output directory for DICOM files",
required=True)
parser.add_argument("--niftidir", help="Root output directory for NIFTI files", required=True)
parser.add_argument("--overwrite", help="Overwrite NIFTI files if they exist")
parser.add_argument("--upload-by-ref", help="Upload \"by reference\". Only use if your host can read your file system.")
parser.add_argument("--workflowId", help="Pipeline workflow ID")
parser.add_argument('--version', action='version', version='%(prog)s 1')

args, unknown_args = parser.parse_known_args()
host = cleanServer(args.host)
session = args.session
subject = args.subject
project = args.project
overwrite = isTrue(args.overwrite)
topupdir = args.topupdir
niftidir = args.niftidir
workflowId = args.workflowId
uploadByRef = isTrue(args.upload_by_ref)
#dcm2niixArgs = unknown_args if unknown_args is not None else []

print ("Args: {}".format(args))

#builddir = os.getcwd()

## Set up working directory
#if not os.access(dicomdir, os.R_OK):
    #print 'Making DICOM directory %s' % dicomdir
    #os.mkdir(dicomdir)
#if not os.access(niftidir, os.R_OK):
    #print 'Making NIFTI directory %s' % niftidir
    #os.mkdir(niftidir)
#if not os.access(imgdir, os.R_OK):
    #print 'Making NIFTI image directory %s' % imgdir
    #os.mkdir(imgdir)
#if not os.access(bidsdir, os.R_OK):
    #print 'Making NIFTI BIDS directory %s' % bidsdir
    #os.mkdir(bidsdir)

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
scanIDList = [scan['ID'] for scan in scanRequestResultList]
seriesDescList = [scan['series_description'] for scan in scanRequestResultList]

### creating dico files
dict_dico_files = {}

index_run = 0

for scan in scanRequestResultList:

    scanid = scan['ID']
    ## Get scan resources
    print "Get scan nifti for scan %s." % scanid
    r = get(host + "/data/experiments/%s/scans/%s/resources" % \
        (session, scanid), params={"format": "json"})
    scanResources = r.json()["ResultSet"]["Result"]
    scanLabels = [res["label"] for res in scanResources]
    print 'Found labels %s.' % ', '.join(scanLabels)
    print 'Found labels {}'.format(scanLabels)
    ###########
    if "NIFTI" in scanLabels and "BIDS" in scanLabels:
        splitdesc =  scan['series_description'].split("_")

        print("Scan {} (desc = {}) a BIDS structure".format(
            scanid,scan['series_description'] ))

        if ("PA" in splitdesc or "AP" in splitdesc) and "topup" in splitdesc:

            for desc in splitdesc:
                if desc.startswith("Fieldmap") and desc[-1].isdigit():

                    index_run = desc[-1]

            print(index_run)

            for label in ["BIDS","NIFTI"]:
                resourceDict = {
                    resource['format']:resource['xnat_abstractresource_id'] \
                        for resource in r.json()["ResultSet"]["Result"]}

                path_res = "/data/experiments/%s/scans/%s/resources/"%(
                    session,scanid)
                filesURL_json=host+path_res+label+"/files"


                r_files = get(filesURL_json, params={"format": "json"})
                print(r_files)

                pathDict = [val for val in \
                    r_files.json()["ResultSet"]["Result"]][0]
                print (pathDict)


                ### copying file
                loc_file = os.path.join(niftidir,pathDict['Name'])
                print(loc_file)

                with open(loc_file, 'wb') as f:
                    r_val = get(host + pathDict['URI'], stream=True)
                    for block in r_val.iter_content(1024):
                        if not block:
                            break
                        f.write(block)

                print 'Downloaded file %s' % pathDict['Name']
                if 'PA' in splitdesc:
                    rad = 'PA'
                elif 'AP' in splitdesc:
                    rad = 'AP'

                if label == "NIFTI":
                    suf  = "fmap"
                elif label == "BIDS":
                    suf = "json"

                key = "{}_{}_file".format(suf,rad)

                if index_run in dict_dico_files.keys():
                    assert key not in dict_dico_files[index_run].keys(), \
                        "Error, {} should not already be in {}".format(
                            key, index_run)

                else:

                    print("Creating {} ".format(index_run))

                    dict_dico_files[index_run] = {}

                print("Adding {} to {} {}".format(loc_file, index_run, key))

                dict_dico_files[index_run][key] = loc_file

        #### otherwise, keep the name for "IntendedFor"
        #if splitdesc[-1] != "SBRef" and splitdesc[0].startswith("task-"):
        ##if splitdesc[-1] == "bold" and splitdesc[0].startswith("task-"):

            #path_res = "/data/experiments/%s/scans/%s/resources/"%(session,scanid)

            #print("Found a fonctional file {} to add to 'intended for' field".format(path_res))

            #filesURL_json=host+path_res+"NIFTI/files"
            #print(filesURL_json)

            #r_files = get(filesURL_json, params={"format": "json"})
            #print(r_files.json())

            #if "ResultSet" not in r_files.json():
                #continue

            #if "Result" not in r_files.json()["ResultSet"]:
                #continue

            #if len(r_files.json()["ResultSet"]["Result"]) == 0:
                #continue

            #pathDict = [val for val in
                        #r_files.json()["ResultSet"]["Result"]][0]

            #if 'Name' in pathDict.keys():
                #split_subject = subject.split("_")
                #print("*** {}".format(split_subject))

                #print(splitdesc)

                #done = False
                #for desc in splitdesc:
                    ## special localizer
                    #if done:
                        #continue

                    #elif desc == "task-Localizer":
                        #print("Found task-Localizer, forcing run = 1 for IntendedFor")

                        #index_run = '1'
                        #done = True

                    #elif desc.startswith("run") and desc[-1].isdigit():
                        #index_run = desc[-1]
                        #done = True

                #print(index_run)

                #if len(split_subject) > 1:
                    #if split_subject[1].startswith("ses"):
                        #print("**** Adding {} for IntendedFor".format(split_subject))
                        #task_bold_file = os.path.join(split_subject[1],"func",pathDict['Name'])
                    #else:
                        #print("Not starting with ses {} for IntendedFor".format(split_subject))
                        #task_bold_file = os.path.join("func",pathDict['Name'])
                #else:

                    #print("Not starting with ses {} for IntendedFor".format(split_subject))
                    #task_bold_file = os.path.join("func",pathDict['Name'])

                #if not index_run in dict_dico_files.keys():

                    #print("Creating {} ".format(index_run))

                    #dict_dico_files[index_run] = {}

                #if 'IntendedFor' in dict_dico_files[index_run].keys():
                    #dict_dico_files[index_run]['IntendedFor'].append(task_bold_file)
                #else:
                    #dict_dico_files[index_run]['IntendedFor'] = [task_bold_file]

                #print("IntendedFor: {}".format(dict_dico_files[index_run]['IntendedFor']))

print("Finished dict_dico_files")
^
print(dict_dico_files)

print("*********************** now running topup part of the script")

for index, dico_files in dict_dico_files.iteritems():

    index = "_run-"+str(index)

    print("**** running topup for {}".format(index))

    fmap_AP_file = dico_files["fmap_AP_file"]
    fmap_PA_file = dico_files["fmap_PA_file"]

    json_AP_file = dico_files["json_AP_file"]
    json_PA_file = dico_files["json_PA_file"]

    print("Ok for fieldmap and json files")

    fmap_AP_PA_file = os.path.join(topupdir,subject + "_FieldmapAP_PA"+ index)

    assert os.path.exists(fmap_AP_file), "Could not find fmap_AP_file = {}".format(fmap_AP_file)
    assert os.path.exists(fmap_PA_file), "Could not find fmap_PA_file = {}".format(fmap_PA_file)

    cmd = "fslmerge -t {} {} {}".format(fmap_AP_PA_file, fmap_AP_file, fmap_PA_file)

    print(cmd)

    os.system(cmd)

    ################### removing one slice if necessary
    assert os.path.exists(fmap_AP_PA_file+".nii.gz"), "Could not find fmap_AP_PA_file = {}.nii.gz".format(fmap_AP_PA_file)

    dimz = os.popen('fslval {}.nii.gz dim3'.format(fmap_AP_PA_file)).read()

    print("dimz = {}".format(dimz))
    if int(dimz)%2 == 1:

        print("Remove one slice from data to get even number of slices")
        tmp_file = os.path.join(topupdir,"up_down_b0"+index)

        os.system("fslroi {}.nii.gz {}.nii.gz  0 -1 0 -1 1 -1".format(fmap_AP_PA_file, tmp_file))
        fmap_AP_PA_file = tmp_file
        dimz = os.popen('fslval {}.nii.gz dim3'.format(fmap_AP_PA_file)).read()

        print("After modif, dimz = {}".format(dimz))

    print (fmap_AP_PA_file)

    #dimz=`fslval $out_dir/topup/Fieldmap_AP_PA.nii.gz dim3`
    #if [ `expr $dimz % 2` -eq 1 ]; then
        #"Remove one slice from data to get even number of slices"
        #fslroi $out_dir/topup/up_down_b0.nii.gz $out_dir/topup/Fieldmap_AP_PA.nii.gz 0 -1 0 -1 1 -1
    #fi


    ################################## RT and AP
    with open(json_AP_file) as f:
        json_AP = json.load(f)

        RT= json_AP["TotalReadoutTime"]
        direction_AP=json_AP['PhaseEncodingDirection']

    if direction_AP == "j-":
        phase_AP = '0 -1 0'

    elif direction_AP == "j":
        phase_AP = '0 1 0'

    else:
        print("Error, wrong encoding dir for AP (now {})".format(direction_AP))

    ###################################### PA
    with open(json_PA_file) as f:
        json_PA = json.load(f)

        direction_PA=json_PA['PhaseEncodingDirection']

        #assert direction_AP == "j-", \
            #("Error, AP file should be 'j-' (now {})".format(direction_AP))

    if direction_PA == "j-":
        phase_PA = '0 -1 0'

    elif direction_PA == "j":
        phase_PA = '0 1 0'

    else:
        print("Error, wrong encoding dir for PA (now {})".format(direction_PA))

    assert phase_AP != phase_PA, "Error, both phase cannot be equal"

    ##################################### Running
    param_AP_PA_file = os.path.join(topupdir, subject+index + "_acqparamsAP_PA.txt")

    with open(param_AP_PA_file,"w+") as f:
        f.write("{} {}\n{} {}\n{} {}\n{} {}\n{} {}\n{} {}\n".format(
            phase_AP, RT, phase_AP, RT, phase_AP, RT, phase_PA, RT, phase_PA, RT,
            phase_PA, RT))

    out_file=os.path.join(topupdir,subject+index+"_my_topup_results1")

    #fout_file=os.path.join(topupdir,subject+index+"_acq-topup_fieldmap")
    fout_file=os.path.join(topupdir,subject+index+"_fieldmap")

    iout_file=os.path.join(topupdir,subject+index+"_my_unwarped_images")

    def add_quotes(cur_string):
        return '"' + cur_string + '"'

    ### modify and create associated topup_json
    new_json_contents = {'Units': "Hz"}

    if 'IntendedFor' in dico_files.keys():
        new_json_contents["IntendedFor"] = dico_files['IntendedFor']

    #topup_json_file=os.path.join(topupdir,subject+"_acq-topup_fieldmap.json")
    topup_json_file=os.path.join(topupdir,subject+index+"_fieldmap.json")

    with open(json_AP_file) as f:
        data = json.load(f)

    data.update(new_json_contents)

    with open(topup_json_file, 'w+') as f:
        json.dump(data, f)

    ## run topup
    cmd = "topup --imain={} --datain={} --config=/data/xnat/pipeline/catalog/TopUp_Auto/resources/b02b0.cnf --out={} --fout={} --iout={}".format(
        fmap_AP_PA_file, param_AP_PA_file, out_file, fout_file, iout_file)
    print ("Running topup {}".format(cmd))

    #val = os.system("topup --imain={} --datain={} --out={} --fout={} --iout={}".format(
        #fmap_AP_PA_file, param_AP_PA_file, out_file, fout_file, iout_file))

    val = os.system(cmd)

    ##  ## to check
    print ("Topup results : {}".format(val))

    ## average images:
    print ("Average magnitude images")

    #mag_file = os.path.join(topupdir,subject+index+"_acq-topup_magnitude")
    mag_file = os.path.join(topupdir,subject+index+"_magnitude")

    os.system ("fslmaths {} -Tmean {}".format(iout_file, mag_file))

    ###############################################################################
    #Upload TOPUP_JSON results
    try:
        queryArgs = {}

        #if workflowId is not None:
            #queryArgs["event_id"] = workflowId

        r = sess.delete(
            host+"/data/experiments/%s/reconstructions/TOPUP_JSON_%s" %(session, subject+index),
            params=queryArgs)

        r.raise_for_status()

    except (requests.ConnectionError, requests.exceptions.RequestException) as e:
        print "There was a problem deleting"
        print "    " + str(e)

    print ('Preparing to upload files for TOPUP_JSON.')

    queryArgs={"type":"TOPUP_JSON"}
    r = sess.put(host+ "/data/experiments/%s/reconstructions/TOPUP_JSON_%s?\
        xnat:reconstructedImageData"%(session, subject+index), params=queryArgs)
    r.raise_for_status()

    ## Uploading
    queryArgs = {"format": "JSON", "content": "TOPUP_JSON", "tags": "TOPUP"}

    #if workflowId is not None:
        #queryArgs["event_id"] = workflowId

    #if uploadByRef:
    queryArgs["reference"] = topup_json_file
    r = sess.put(host+"/data/experiments/%s/reconstructions/TOPUP_JSON_%s/files"%(session, subject+index), params=queryArgs)
    r.raise_for_status()

    # Upload TOPUP_FMAP results
    try:
        queryArgs = {}
        #if workflowId is not None:
            #queryArgs["event_id"] = workflowId
        r = sess.delete(host+"/data/experiments/%s/reconstructions/TOPUP_FMAP_%s" % (
            session, subject+index), params=queryArgs)
        r.raise_for_status()

    except (requests.ConnectionError, requests.exceptions.RequestException) as e:
        print "There was a problem deleting"
        print "    " + str(e)

    print ('Preparing to upload files for TOPUP_FMAP.')

    queryArgs={"type":"TOPUP_FMAP"}
    r = sess.put(host+ "/data/experiments/%s/reconstructions/TOPUP_FMAP_%s?\
        xnat:reconstructedImageData"%(session, subject+index),params=queryArgs)
    r.raise_for_status()

    ## Uploading
    queryArgs = {"format": "NIFTI", "content": "TOPUP_FMAP", "tags": "TOPUP"}

    #if workflowId is not None:
        #queryArgs["event_id"] = workflowId

    #if uploadByRef:
    queryArgs["reference"] = fout_file  + ".nii.gz"
    r = sess.put(host+"/data/experiments/%s/reconstructions/TOPUP_FMAP_%s/files"%(
        session, subject+index), params=queryArgs)
    r.raise_for_status()

    # Upload TOPUP_MAG results
    try:
        queryArgs = {}

        #if workflowId is not None:
            #queryArgs["event_id"] = workflowId

        r = sess.delete(host+"/data/experiments/%s/reconstructions/TOPUP_MAG_%s" % (
            session, subject+index), params=queryArgs)
        r.raise_for_status()

    except (requests.ConnectionError, requests.exceptions.RequestException) as e:
        print "There was a problem deleting"
        print "    " + str(e)

    print ('Preparing to upload files for TOPUP_MAG.')

    queryArgs={"type":"TOPUP_MAG"}
    r = sess.put(host+ "/data/experiments/%s/reconstructions/TOPUP_MAG_%s?\
        xnat:reconstructedImageData"%(session, subject+index),params=queryArgs)
    r.raise_for_status()

    ## Uploading
    queryArgs = {"format": "NIFTI", "content": "TOPUP_MAG", "tags": "TOPUP"}

    #if workflowId is not None:
        #queryArgs["event_id"] = workflowId

    #if uploadByRef:
    queryArgs["reference"] = mag_file  + ".nii.gz"
    r = sess.put(host+"/data/experiments/%s/reconstructions/TOPUP_MAG_%s/files"%(
        session, subject+index), params=queryArgs)
    r.raise_for_status()

    # All done
    print (workflowId,session)
    print 'All done with session-level metadata.'
