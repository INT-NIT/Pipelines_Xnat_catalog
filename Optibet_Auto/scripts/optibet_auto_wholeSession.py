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

        if "T1w" in splitdesc:

            resourceDict = {
                resource['format']:resource['xnat_abstractresource_id'] \
                    for resource in r.json()["ResultSet"]["Result"]}
            print(resourceDict)

            path_res = "/data/experiments/%s/scans/%s/resources/"%(
                session,scanid)
            filesURL_json=host+path_res+"NIFTI/files"


            r_files = get(filesURL_json, params={"format": "json"})
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

            os.system("/bin/bash /data/xnat/pipeline/catalog/Optibet_Auto/resources/optiBET.sh -i {} ".format(loc_file))

################################################################################
#Upload T1_OPTIBET_BRAIN results

optibet_brain_file = os.path.join(niftidir,subject+'_T1w_optiBET_brain.nii.gz')

try:
    queryArgs = {}
    if workflowId is not None:
        queryArgs["event_id"] = workflowId

    r = sess.delete(
        host+"/data/experiments/%s/reconstructions/T1_OPTIBET_BRAIN" %session,
        params=queryArgs)

    r.raise_for_status()

except (requests.ConnectionError, requests.exceptions.RequestException) as e:
    print "There was a problem deleting"
    print "    " + str(e)

print ('Preparing to upload files for T1_OPTIBET_BRAIN.')

queryArgs={"type":"T1_OPTIBET_BRAIN"}
r = sess.put(host+ "/data/experiments/%s/reconstructions/T1_OPTIBET_BRAIN?\
    xnat:reconstructedImageData"%(session),params=queryArgs)
r.raise_for_status()

## Uploading
queryArgs = {"format": "JSON", "content": "T1_OPTIBET_BRAIN", "tags": "OPTIBET"}

if workflowId is not None:
    queryArgs["event_id"] = workflowId

#if uploadByRef:
queryArgs["reference"] = optibet_brain_file
r = sess.put(host+"/data/experiments/%s/reconstructions/T1_OPTIBET_BRAIN/files"%(
    session), params=queryArgs)
r.raise_for_status()

############### T1_OPTIBET_BRAIN_MASK

optibet_brain_mask_file = os.path.join(niftidir,subject+'_T1w_optiBET_brain_mask.nii.gz')
# Upload T1_OPTIBET_BRAIN_MASK results
try:
    queryArgs = {}
    if workflowId is not None:
        queryArgs["event_id"] = workflowId
    r = sess.delete(host+"/data/experiments/%s/reconstructions/T1_OPTIBET_BRAIN_MASK" % (
        session), params=queryArgs)
    r.raise_for_status()

except (requests.ConnectionError, requests.exceptions.RequestException) as e:
    print "There was a problem deleting"
    print "    " + str(e)

print ('Preparing to upload files for T1_OPTIBET_BRAIN_MASK.')

queryArgs={"type":"T1_OPTIBET_BRAIN_MASK"}
r = sess.put(host+ "/data/experiments/%s/reconstructions/T1_OPTIBET_BRAIN_MASK?\
    xnat:reconstructedImageData"%(session),params=queryArgs)
r.raise_for_status()

## Uploading
queryArgs = {"format": "NIFTI", "content": "T1_OPTIBET_BRAIN_MASK", "tags": "OPTIBET"}

if workflowId is not None:
    queryArgs["event_id"] = workflowId

#if uploadByRef:
queryArgs["reference"] = optibet_brain_mask_file
r = sess.put(host+"/data/experiments/%s/reconstructions/T1_OPTIBET_BRAIN_MASK/files"%(
    session), params=queryArgs)
r.raise_for_status()

## All done
print 'All done with session-level metadata.'
