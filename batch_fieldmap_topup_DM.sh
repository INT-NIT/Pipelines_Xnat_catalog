#!/bin/bash

source ../Topup_Physio_BIDS/subjects_to_process.cfg
EXPDIR=/Volumes/groupdata/MRI_BIDS_DATABANK/$study
mkdir -p /Volumes/groupdata/MRI_BIDS_DATABANK/$study/derivatives
for sub in $list_sub
do
    echo -----starting subject $sub------
    OUTDIR=/Volumes/groupdata/MRI_BIDS_DATABANK/$study/derivatives/$sub
    SUBDIR=$EXPDIR/$sub/
    mkdir -p /Volumes/groupdata/MRI_BIDS_DATABANK/$study/derivatives
    mkdir -p ${OUTDIR}
    mkdir -p ${OUTDIR}/topup

    fslmerge -t "${OUTDIR}/topup/${sub}_FieldmapAP_PA"  "${SUBDIR}fmap/${sub}_acq-topup_dir-01_epi.nii.gz" "${SUBDIR}fmap/${sub}_acq-topup_dir-02_epi.nii.gz"

    RT=$(jq .TotalReadoutTime "${SUBDIR}fmap/${sub}_acq-topup_dir-01_epi.json")

    direction1=$(jq '.PhaseEncodingDirection' "${SUBDIR}fmap/${sub}_acq-topup_dir-01_epi.json")
    direction2=$(jq '.PhaseEncodingDirection' "${SUBDIR}fmap/${sub}_acq-topup_dir-02_epi.json")
    if [ "$direction1" = '"j-"' ]; then
        phase1='0 -1 0'
    elif [ "$direction1" = '"j"' ]; then
        phase1='0 1 0'
    fi;
    if [ "$direction2" = '"j-"' ]; then
        phase2='0 -1 0'
    elif [ "$direction2" = '"j"' ]; then
        phase2='0 1 0'
    fi;
    echo   "$phase1 $RT\n$phase1 $RT\n$phase1 $RT\n$phase2 $RT\n$phase2 $RT\n$phase2 $RT" > ${OUTDIR}/topup/${sub}_acqparamsAP_PA.txt
    topup --imain="${OUTDIR}/topup/${sub}_FieldmapAP_PA" --datain="${OUTDIR}/topup/${sub}_acqparamsAP_PA.txt" --config=b02b0.cnf --out="${OUTDIR}/topup/${sub}_my_topup_results1" --fout="${SUBDIR}fmap/${sub}_acq-topup_fieldmap" --iout="${OUTDIR}/topup/${sub}_my_unwarped_images"


    jq '. + { "Units": "Hz"}' "${SUBDIR}fmap/${sub}_acq-topup_dir-01_epi.json" > "${OUTDIR}/tmp.$$.json" && mv  "${OUTDIR}/tmp.$$.json" "${SUBDIR}fmap/${sub}_acq-topup_fieldmap.json"


    fslmaths "${OUTDIR}/topup/${sub}_my_unwarped_images" -Tmean "${SUBDIR}fmap/${sub}_acq-topup_magnitude"

    list=$(ls "${SUBDIR}func/"*bold.nii.gz)
    list_func=${list//$SUBDIR/}

    for func in $list_func
    do
        jq --arg func $func  '.IntendedFor |= .+  [$func]' "${SUBDIR}"fmap/"${sub}_acq-topup_fieldmap.json"  > "${OUTDIR}/tmp.$$.json" && mv  "${OUTDIR}/tmp.$$.json" "${SUBDIR}"fmap/"${sub}_acq-topup_fieldmap.json"
    done
done

