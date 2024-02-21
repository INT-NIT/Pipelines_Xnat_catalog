#!/usr/bin/env python

"""xnat2bids
Turn files in XNAT archive format into BIDS format.

Usage:
    xnat2bids.py <inputDir> <outputDir> <subjects>...
    xnat2bids.py (-h | --help)
    xnat2bids.py --version

Options:
    -h --help           Show the usage
    --version           Show the version
    <inputDir>          Directory with XNAT-archive-formatted files.
                        There should be scan directories, each having a NIFTI
resource with NIFTI files, and
                        BIDS resources with BIDS sidecar JSON files.
    <outputDir>         Directory in which BIDS formatted files should be written.
    <subjects>          List of subjects
"""

import os
import sys
import json
import shutil
from glob import glob
from docopt import docopt

bidsAnatModalities = ['t1w', 't2w', 't1rho', 't1map', 't2map', 't2star', 'flair', 'flash', 'pd', 'pdmap', 'pdw', 'pdt2', 'inplanet1', 'inplanet2', 'angio', 'defacemask', 'swimagandphase', 'mprage', 'mp2rage', 'unit1', 'petra']
bidsFuncModalities = ['bold', 'physio', 'stim', 'sbref', 'events', 'eyedata', 'physiolog']
bidsDwiModalities = ['dwi', 'dti']
bidsBehavioralModalities = ['beh']
bidsFieldmapModalities = ['phasemap', 'magnitude1','fieldmap','magnitude','epi']


def generateBidsNameMap(bidsFileName):

    # The BIDS file names will look like
    # sub-<participant_label>[_ses-<session_label>][_acq-<label>][_ce-<label>][_rec-< label>][_run-<index>][_mod-<label>]_<modality_label>
    # (that example is for anat. There may be other fields and labels in the other file types.)
    # So we split by underscores to get the individual field values.
    # However, some of the values may contain underscores themselves, so we have to check that each entry (save the last)
    #   contains a -.
    underscoreSplitListRaw = bidsFileName.split('_')
    underscoreSplitList = []

    for splitListEntryRaw in underscoreSplitListRaw[:-1]:
        ## special Regis
        if splitListEntryRaw.split("-")[0] == "dir":
            splitListEntryRaw="acq-"+splitListEntryRaw.split("-")[1]

        if '-' not in splitListEntryRaw:
            underscoreSplitList[-1] = underscoreSplitList[-1] + splitListEntryRaw
        else:
            underscoreSplitList.append(splitListEntryRaw)

    bidsNameMap = dict(splitListEntry.split('-') for splitListEntry in underscoreSplitList)
    bidsNameMap['modality'] = underscoreSplitListRaw[-1]

    return bidsNameMap


class BidsScan(object):
    def __init__(self, scanId, bidsNameMap, *args):
        self.scanId = scanId
        self.bidsNameMap = bidsNameMap
        self.subject = bidsNameMap.get('sub')
        self.modality = bidsNameMap.get('modality')
        modalityLowercase = self.modality.lower()
        self.subDir = 'anat' if modalityLowercase in bidsAnatModalities else \
                      'func' if modalityLowercase in bidsFuncModalities else \
                      'dwi' if modalityLowercase in bidsDwiModalities else \
                      'beh' if modalityLowercase in bidsBehavioralModalities else \
                      'fmap' if modalityLowercase in bidsFieldmapModalities else \
                      None
        self.sourceFiles = list(args)

class BidsSession(object):
    def __init__(self, sessionLabel, bidsScans=[]):

        print(sessionLabel)

        bids_name_map = {}

        for i,splitListEntry in enumerate(sessionLabel.split("_")):
            split_sess = splitListEntry.split('-')

            if len(split_sess) == 2:
                key,val = split_sess
                bids_name_map[key] = val

            elif len(split_sess) == 1:
                if i == 0:
                    bids_name_map["sub"] = splitListEntry

                elif i == 1:
                    if splitListEntry.startswith("MR"):
                        bids_name_map["ses"] = "{:02d}".format(int(splitListEntry[2:]))
                    else:
                        bids_name_map["ses"] = splitListEntry

                else:
                    print("Unable to find given suffix for {}".format(splitListEntry))
                    0/0


        print(bids_name_map)

        self.sessionLabel = bids_name_map['ses']
        self.bidsScans = bidsScans

class BidsEvents(object):
    def __init__(self, bidsEvents=[]):
        self.sessionLabel = sessionLabel
        self.bidsEvents = bidsEvents

class BidsSubject(object):
    def __init__(self, subjectLabel, bidsSession=None, bidsScans=[]):
        self.subjectLabel = subjectLabel
        if bidsSession:
            self.bidsSessions = [bidsSession]
            self.bidsScans = None
        if bidsScans:
            self.bidsScans = bidsScans
            self.bidsSessions = None

    def addBidsSession(self, bidsSession):
        if self.bidsScans:
            raise ValueError("Cannot add a BidsSession when the subject already has a list of BidsScans.")
        if not self.bidsSessions:
            self.bidsSessions = []
        self.bidsSessions.append(bidsSession)

    def hasSessions(self):
        return bool(self.bidsSessions is not None and self.bidsSessions is not
[])

    def hasScans(self):
        return bool(self.bidsScans is not None and self.bidsScans is not [])

def bidsifySession(sessionDir):
    print("Checking for session structure in " + sessionDir)

    sessionBidsJsonPath = os.path.join(sessionDir, 'RESOURCES', 'BIDS', 'dataset_description.json')

    scansDir = os.path.join(sessionDir, 'SCANS')
    if not os.path.exists(scansDir):
        # I guess we don't have any scans with BIDS data in this session
        print("STOPPING. Could not find SCANS directory.")
        return

    print("Found SCANS directory. Checking scans for BIDS data.")

    bidsScans = []

    for scanId in os.listdir(scansDir):
        print("")
        print("Checking scan {}.".format(scanId))

        scanDir = os.path.join(scansDir, scanId)
        scanBidsDir = os.path.join(scanDir, 'BIDS')
        scanNiftiDir = os.path.join(scanDir, 'NIFTI')

        if not os.path.exists(scanBidsDir):
            # This scan does not have BIDS data
            print("SKIPPING. Scan {} does not have a BIDS directory.".format(scanId))
            continue

        print("checking img and json BIDS")
        scanBidsJsonGlobList = glob(scanBidsDir + '/*.json')

        #### Removed checks of several JSON (multi-echo is allowed)
        for scanBidsJsonFilePath in scanBidsJsonGlobList:

            scanBidsJsonFileName = os.path.basename(scanBidsJsonFilePath)
            scanBidsFileName = scanBidsJsonFileName.rstrip('.json')
            scanBidsNameMap = generateBidsNameMap(scanBidsFileName)

            print("BIDS JSON file name: {}".format(scanBidsJsonFileName))
            print("Name map: {}".format(scanBidsNameMap))

            if not scanBidsNameMap.get('sub') or not scanBidsNameMap.get('modality'):
                # Either 'sub' or 'modality' or both weren't found. Something is wrong. Let's find out what.
                if not scanBidsNameMap.get('sub') and not scanBidsNameMap.get('modality'):
                    print("SKIPPING. Neither 'sub' nor 'modality' could be parsed from the BIDS JSON file name.")

                elif not scanBidsNameMap.get('sub'):
                    print("SKIPPING. Could not parse 'sub' from the BIDS JSON file name.")

                else:
                    print("SKIPPING. Could not parse 'modality' from the BIDS JSON file name.")

                continue

            scanBidsDirFilePaths = glob(os.path.join(scanBidsDir, scanBidsFileName) + '.*')
            scanNiftiDirFilePaths = glob(os.path.join(scanNiftiDir, scanBidsFileName) + '.*')
            allFilePaths = scanBidsDirFilePaths + scanNiftiDirFilePaths

            bidsScan = BidsScan(scanId, scanBidsNameMap, *allFilePaths)
            if not bidsScan.subDir:
                print("SKIPPING. Could not determine subdirectory for modality {}.".format(bidsScan.modality))
                continue

            bidsScans.append(bidsScan)


        print("checking dcm BIDS")
        scanBidsDcmGlobList = glob(scanBidsDir + '/*.dcm')

        if len(scanBidsDcmGlobList):

            #### checking only dcm file
            assert len(scanBidsDcmGlobList) == 1, "PhysioLog dir should have only one dcm file, {}".format(scanBidsDcmGlobList)

            scanBidsDcmFilePath = scanBidsDcmGlobList[0]

            print(scanBidsDcmFilePath )
            scanBidsDcmFileName = os.path.basename(scanBidsDcmFilePath)
            scanBidsFileName = scanBidsDcmFileName.rstrip('.dcm')

            scanBidsNameMap = generateBidsNameMap(scanBidsFileName)

            print(scanBidsNameMap)

            bidsScan = BidsScan(scanId, scanBidsNameMap,scanBidsDcmFilePath )

            if not bidsScan.subDir:
                print("SKIPPING. Could not determine subdirectory for modality {}.".format(bidsScan.modality))
                continue

            bidsScans.append(bidsScan)



        print("Done checking scan {}.".format(scanId))

    print("")
    print("Done checking all scans.")


    ### added PROCESSED path
    sessionReconstructedDir = os.path.join(sessionDir, 'PROCESSED')

    if not os.path.exists(sessionReconstructedDir):
        # This scan does not have BIDS data
        print("No Reconconstruction (PROCESSED) directory for {}".format(sessionDir))

    else:

        for reconDir in os.listdir(sessionReconstructedDir):
            print("")
            print("Checking recon {}.".format(reconDir))

            date_ids = [curdir for curdir in os.listdir(os.path.join(sessionReconstructedDir, reconDir))]

            assert len(date_ids) > 0, "Error, Found reconDir in {}, but is empty".format(sessionReconstructedDir)

            ### sorted by date if several
            sorted_dates = sorted(date_ids)

            print(sorted_dates)

            print([dir for dir in os.listdir(os.path.join(sessionReconstructedDir, reconDir, sorted_dates[-1]))])

            recon_files = glob(os.path.join(sessionReconstructedDir, reconDir, sorted_dates[-1],"sub-*"))

            if len(recon_files) == 0:
                continue

            ### we take the last one
            reconFileName = recon_files[0]

            print(reconFileName)

            baseReconFileName = os.path.basename(reconFileName)
            reconFileNameNameMap = generateBidsNameMap(baseReconFileName.split('.')[0])

            if not reconFileNameNameMap.get('sub') or not reconFileNameNameMap.get('modality'):
                # Either 'sub' or 'modality' or both weren't found. Something is wrong. Let's find out what.
                if not reconFileNameNameMap.get('sub') and not reconFileNameNameMap.get('modality'):
                    print("SKIPPING. Neither 'sub' nor 'modality' could be parsed from the BIDS file name.")

                elif not reconFileNameNameMap.get('sub'):
                    print("SKIPPING. Could not parse 'sub' from the BIDS file name.")

                else:
                    print("SKIPPING. Could not parse 'modality' from the BIDS file name.")

                continue

            bidsScan = BidsScan(scanId, reconFileNameNameMap, reconFileName)
            if not bidsScan.subDir:
                print("SKIPPING. Could not determine subdirectory for modality {}.".format(bidsScan.modality))
                continue

            bidsScans.append(bidsScan)

    ############ adding local resources
    sessionBidsRessourcePath = os.path.join(sessionDir, 'RESOURCES', 'sourcedata')


    if not os.path.exists(sessionBidsRessourcePath):
        # This scan does not have BIDS data
        print("No local sourcedata (RESOURCES/sourcedata) directory for {}".format(sessionDir))

    else:

        for sourceDir in os.listdir(sessionBidsRessourcePath):

            cur_path = os.path.join(sessionBidsRessourcePath, sourceDir)

            if not os.path.isdir(cur_path):
                continue

            print("")
            print("Checking sourcedata {}.".format(sourceDir))

            sourcedata_files = [curdir for curdir in os.listdir(cur_path) ]

            print(sourcedata_files)

            for sourcedata_file in sourcedata_files:

                print(sourcedata_file )
                scanBidsDcmFileName = os.path.basename(sourcedata_file)
                scanBidsFileName = scanBidsDcmFileName.split('.')[0]

                scanBidsNameMap = generateBidsNameMap(scanBidsFileName)

                print(scanBidsNameMap)

                bidsScan = BidsScan(scanId, scanBidsNameMap,os.path.join(cur_path, sourcedata_file))

                if not bidsScan.subDir:
                    print("SKIPPING. Could not determine subdirectory for modality {}.".format(bidsScan.modality))
                    continue

                bidsScans.append(bidsScan)

            ### sorted by date if several
            sorted_dates = sorted(date_ids)

            print(sorted_dates)

            ### we take the last one
            reconFileName = glob(os.path.join(sessionReconstructedDir, reconDir, sorted_dates[-1],"sub-*"))[0]

            print(reconFileName)

            baseReconFileName = os.path.basename(reconFileName)
            reconFileNameNameMap = generateBidsNameMap(baseReconFileName.split('.')[0])

            if not reconFileNameNameMap.get('sub') or not reconFileNameNameMap.get('modality'):
                # Either 'sub' or 'modality' or both weren't found. Something is wrong. Let's find out what.
                if not reconFileNameNameMap.get('sub') and not reconFileNameNameMap.get('modality'):
                    print("SKIPPING. Neither 'sub' nor 'modality' could be parsed from the BIDS file name.")

                elif not reconFileNameNameMap.get('sub'):
                    print("SKIPPING. Could not parse 'sub' from the BIDS file name.")

                else:
                    print("SKIPPING. Could not parse 'modality' from the BIDS file name.")

                continue

            bidsScan = BidsScan(scanId, reconFileNameNameMap, reconFileName)
            if not bidsScan.subDir:
                print("SKIPPING. Could not determine subdirectory for modality {}.".format(bidsScan.modality))
                continue

            bidsScans.append(bidsScan)





    return bidsScans,sessionBidsJsonPath


def getSubjectForBidsScans(bidsScanList):
    print("")
    print("Finding subject for list of BIDS scans.")

    subjects=[]

    for bidsScan in bidsScanList:

        print(bidsScan.scanId)
        print(bidsScan.subject)

        print(bidsScan.bidsNameMap)
        print(bidsScan.modality)

        if bidsScan.subject:
            if bidsScan.subject not in subjects:
                subjects.append(bidsScan.subject)

    if len(subjects) == 1:
        print("Found subject {}.".format(subjects[0]))
        return subjects[0]
    elif len(subjects) > 1:
        print("ERROR: Found more than one subject: {}.".format(", ".join(subjects)))
    else:
        print("ERROR: Found no subjects.")

    return None

def copyScanBidsFiles(destDirBase, bidsScanList):
    # First make all the "anat", "func", etc. subdirectories that we will need
    for subDir in {scan.subDir for scan in bidsScanList}:
        os.mkdir(os.path.join(destDirBase, subDir))

    # Now go through all the scans and copy their files into the correct subdirectory
    for scan in bidsScanList:
        destDir = os.path.join(destDirBase, scan.subDir)
        for f in scan.sourceFiles:
            shutil.copy(f, destDir)

##########################################################################
## merging json dataset_description
def mergeJsonDescriptionFiles(sessionJsonDescripFiles,outputDir, inputDir):
    assert len(sessionJsonDescripFiles) != 0, \
        "Error, no description file was found, skipping"

    data = {}

    for json_descrip_file in sessionJsonDescripFiles:
        with open(json_descrip_file) as f:
            new_json_contents = json.load(f)

            #for key,val in new_json_contents:
                #if data[key] != new_json_contents[key]
            if data:
                data["DatasetDOI"].append(new_json_contents["DatasetDOI"])
            else:
                data = new_json_contents
                data["DatasetDOI"]= [new_json_contents["DatasetDOI"]]

    if len(data["DatasetDOI"]) > 1:
        data["DatasetDOI"]= inputDir
    elif len(data["DatasetDOI"]) == 1:
        data["DatasetDOI"]= data["DatasetDOI"][0]

    dataset_decription_file = os.path.join(
        outputDir, "dataset_description.json")

    print(data)

    with open(dataset_decription_file, 'w+') as f:
        json.dump(data, f)

def bidsifySourceData(sessionDir,subSessionBidsScans):
    print("Checking for events structure in " + sessionDir)

    if not os.path.exists(sessionDir):
        print("Could not find {}".format(sessionDir))
        return

    #events_files = glob(os.path.join(sessionDir,"*_events.tsv"))

    #print(events_files)

    #for bidsScan in subSessionBidsScans:
        #print(bidsScan.bidsNameMap)
        #if "task" in bidsScan.bidsNameMap.keys() and \
            #bidsScan.bidsNameMap["modality"] == "bold":

                #for sourceFile in bidsScan.sourceFiles:
                    #if sourceFile.endswith("_bold.nii.gz"):
                        #root_sfile = os.path.basename(
                            #sourceFile.rstrip("_bold.nii.gz"))

                        #print (root_sfile)

                        #expect_ev_file = os.path.join(sessionDir,
                                                      #root_sfile+"_events.tsv")
                        #print(expect_ev_file)
                        #print(events_files)

                        #if expect_ev_file in events_files:
                            #print ("Found corresponding event file")
                            #bidsScan.sourceFiles.append(expect_ev_file)

    #other_files = glob(os.path.join(sessionDir,"*[!_events.tsv]"))
    #print ([bidsScan.sourceFiles for bidsScan in subSessionBidsScans])

    #return other_files

    all_files = glob(os.path.join(sessionDir,"*"))

    return all_files

##########################################################################
version = "1.0"
args = docopt(__doc__, version=version)

inputDir = args['<inputDir>']
outputDir = args['<outputDir>']

subjects = args['<subjects>']

print("**********************************************************************")
print("Input dir: {}".format(inputDir))
print("Output dir: {}".format(outputDir))
print("Subjects: {}".format(subjects))




# First check if the input directory is a session directory
sessionBidsJsonPaths = []
bidsSubjectMap = {}
sourceDataSubjectMap = {}

arc_dir = os.path.join(inputDir,"arc001")
res_dir = os.path.join(inputDir,"resources","sourcedata")

print("Archive dir: {}".format(arc_dir))

for subSessionDir in os.listdir(arc_dir):

    print(subSessionDir)

    subSessionBidsScans,sessionBidsJsonPath = bidsifySession(
        os.path.join(arc_dir, subSessionDir))

    if subSessionBidsScans:
        subject = getSubjectForBidsScans(subSessionBidsScans)

        cur_sub = "sub-" + subject
        if cur_sub not in subjects:
            print("subject {} was not found in list of desired subjects{}".format(cur_sub, subjects))
            continue

        if not subject:
            print("SKIPPING. Could not determine subject for session \
                {}.".format(subSessionDir))
            continue

        print("Adding BIDS session {} to list for subject \
            {}.".format(subSessionDir, subject))
        bidsSession = BidsSession(subSessionDir, subSessionBidsScans)

        if subject not in bidsSubjectMap:
            bidsSubjectMap[subject] = BidsSubject(subject,
                                                bidsSession=bidsSession)
        else:
            bidsSubjectMap[subject].addBidsSession(bidsSession)
        print(bidsSession.sessionLabel)

        ### source data
        ### checking corresponding subject in sourcedata
        subSourceDataDir = os.path.join(res_dir, "sub-"+ subject, "ses-" + bidsSession.sessionLabel)
        print(subSourceDataDir)

        subject_session = subject + "_" + bidsSession.sessionLabel

        print subject_session
        print("Adding source data session{} to list for subject \
            {}.".format(subSessionDir, subject_session))

        print(os.path.exists(subSourceDataDir))

        if os.path.exists(subSourceDataDir):
            subSessionOthers= bidsifySourceData(subSourceDataDir, subSessionBidsScans)

            if subject_session not in sourceDataSubjectMap:
                sourceDataSubjectMap[subject_session] = subSessionOthers
            else:
                sourceDataSubjectMap[subject_session].extend(subSessionOthers)

        else:
            print("no sourcedata were found")


    else:
        print("No BIDS data found in session {}.".format(subSessionDir))

    if os.path.exists(sessionBidsJsonPath):
        sessionBidsJsonPaths.append(sessionBidsJsonPath)
    else:
        print("Error, {} not found".format(sessionBidsJsonPath))

print(bidsSubjectMap)
print(sourceDataSubjectMap)

print("Almost Done.")

if not bidsSubjectMap:
    print("No BIDS data found anywhere in inputDir {}.".format(inputDir))
    sys.exit(1)

print("")
allHaveSessions = True
allHaveScans = True
for bidsSubject in bidsSubjectMap.itervalues():
    allHaveSessions = allHaveSessions and bidsSubject.hasSessions()
    allHaveScans = allHaveScans and bidsSubject.hasScans()

if not (allHaveSessions ^ allHaveScans):
    print("ERROR: Somehow we have a mix of subjects with explicit sessions and subjects without explicit sessions. We must have either all subjects with sessions, or all subjects without. They cannot be mixed.")
    sys.exit(1)

print("Copying BIDS data.")
for bidsSubject in bidsSubjectMap.itervalues():
    print(bidsSubject.subjectLabel)
    subjectDir = os.path.join(outputDir, "sub-" + bidsSubject.subjectLabel)
    os.mkdir(subjectDir)

    if allHaveSessions:
        #if len(bidsSubject.bidsSessions) > 1:
            #for bidsSession in bidsSubject.bidsSessions:

                #print ("ses-" + bidsSession.sessionLabel)
                #sessionDir = os.path.join(subjectDir,
                                          #"ses-" + bidsSession.sessionLabel)
                #os.mkdir(sessionDir)
                #copyScanBidsFiles(sessionDir, bidsSession.bidsScans)
        #else:
            #copyScanBidsFiles(subjectDir,
                              #bidsSubject.bidsSessions[0].bidsScans)

        ### special Regis:
        for bidsSession in bidsSubject.bidsSessions:
            print("ses-" + bidsSession.sessionLabel)
            sessionDir = os.path.join(
                subjectDir, "ses-" + bidsSession.sessionLabel)

            os.mkdir(sessionDir)
            copyScanBidsFiles(sessionDir, bidsSession.bidsScans)


    else:
        copyScanBidsFiles(subjectDir, bidsSubject.bidsScans)

    print("Merging dataset_description s")
    ## merging json dataset_description
    mergeJsonDescriptionFiles(sessionBidsJsonPaths,outputDir,inputDir)

print("Copying sourcedata")
sourceDir = os.path.join(outputDir, "sourcedata")

os.mkdir(sourceDir)
for subject_session,sourcefiles in sourceDataSubjectMap.iteritems():
    subject, session = subject_session.split("_")

    subjectDir = os.path.join(sourceDir, "sub-" + subject)
    os.mkdir(subjectDir)
    print(subjectDir)

    sesDir = os.path.join(subjectDir, "ses-" + session)
    os.mkdir(sesDir)
    print(sesDir)

    for sourcefile in sourcefiles:
        shutil.copy(sourcefile, sesDir)


print("Done.")
