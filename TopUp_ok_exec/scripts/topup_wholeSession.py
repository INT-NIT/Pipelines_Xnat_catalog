import argparse
#import collections
import json
#import requests
import os
#import glob
import sys
#import subprocess
#import time
#import zipfile
#import tempfile
#import dicom as dicomLib
#from shutil import copy as fileCopy
##from nipype.interfaces.dcm2nii import Dcm2nii
#from collections import OrderedDict
#import requests.packages.urllib3
#requests.packages.urllib3.disable_warnings()

parser = argparse.ArgumentParser(
    description="Run topup_wholeSession on every file in a session")


parser.add_argument("--niftidir", help="niftidir", required=True)
parser.add_argument("--subject_id", help="subject_id", required=True)

args, unknown_args = parser.parse_known_args()
print (args, unknown_args)

niftidir = args.niftidir
subject_id = args.subject_id

fmap_AP_file = os.path.join(niftidir,subject_id + "_dir-AP_epi.nii.gz")
fmap_PA_file = os.path.join(niftidir,subject_id + "_dir-PA_epi.nii.gz")
assert os.path.exists(fmap_AP_file),'Error with {}'.format(fmap_AP_file)
assert os.path.exists(fmap_PA_file),'Error with {}'.format(fmap_PA_file)

json_AP_file = os.path.join(niftidir,subject_id + "_dir-AP_epi.json")
json_PA_file = os.path.join(niftidir,subject_id + "_dir-PA_epi.json")
assert os.path.exists(json_AP_file),'Error with {}'.format(json_AP_file)
assert os.path.exists(json_PA_file),'Error with {}'.format(json_PA_file)

print("Ok for fieldmap and json files")

fmap_AP_PA_file = os.path.join(niftidir,subject_id + "_FieldmapAP_PA")

os.system("fslmerge -t {} {} {}".format(
    fmap_AP_PA_file, fmap_AP_file, fmap_PA_file))

# RT
with open(json_AP_file) as f:
    json_AP = json.load(f)

    RT= json_AP["TotalReadoutTime"]
    direction_AP=json_AP['PhaseEncodingDirection']

    assert direction_AP == "j-", \
        ("Error, AP file should be 'j-' (now {})".format(direction_AP))

phase_AP = '0 -1 0'
phase_PA = '0 1 0'

param_AP_PA_file = os.path.join(niftidir, subject_id + "_acqparamsAP_PA.txt")

with open(param_AP_PA_file,"w+") as f:
    f.write("{} {}\n{} {}\n{} {}\n{} {}\n{} {}\n{} {}\n".format(
        phase_AP, RT, phase_AP, RT, phase_AP, RT, phase_PA, RT, phase_PA, RT,
        phase_PA, RT))

out_file=os.path.join(niftidir,subject_id+"_my_topup_results1")

fout_file=os.path.join(niftidir,subject_id+"_acq-topup_fieldmap")
iout_file=os.path.join(niftidir,subject_id+"_my_unwarped_images")

os.system("topup --imain={} --datain={} --out={} --fout={} \
    --iout={}".format(fmap_AP_PA_file, param_AP_PA_file, out_file, fout_file,
                      iout_file))

## --config=b02b0.cnf ## to check

    #jq '. + { "Units": "Hz"}' "${SUBDIR}fmap/${sub}_acq-topup_dir-01_epi.json"
#> "${OUTDIR}/tmp.$$.json" && mv  "${OUTDIR}/tmp.$$.json"
#"${SUBDIR}fmap/${sub}_acq-topup_fieldmap.json"


    #fslmaths "${OUTDIR}/topup/${sub}_my_unwarped_images" -Tmean
#"${SUBDIR}fmap/${sub}_acq-topup_magnitude"

    #list=$(ls "${SUBDIR}func/"*bold.nii.gz)
    #list_func=${list//$SUBDIR/}

