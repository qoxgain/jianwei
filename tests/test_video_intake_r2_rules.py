"""Generated timestamps/receipts test rules, not industrial learning effects."""
import pytest
from scripts.video_intake_r2_rules import timestamp_summary,candidate_idr_ranges,grade


def test_timestamps_keep_small_clock_jitter_but_report_real_holes():
    good=timestamp_summary([100,100.04,100.079,100.12],.04)
    assert good['timeline_continuous']
    bad=timestamp_summary([100,100.04,100.20,100.24],.04)
    assert not bad['timeline_continuous'] and bad['uncovered_seconds']==pytest.approx(.12)


def test_missing_and_reversed_pts_are_not_filled():
    r=timestamp_summary([1,None,1,.96],.04)
    assert r['frames']==4 and r['missing_pts']==1 and r['non_increasing']==2 and not r['timeline_continuous']


def test_error_excludes_full_gop_and_keeps_actual_source_clock():
    r=candidate_idr_ranges([100,102,104,106],[102.8],100,108,.08)
    assert r['candidate_clean_ranges']==[{'start':100,'end':102},{'start':104,'end':108}]
    assert not r['strictly_validated']


def test_no_idr_and_error_on_boundary_do_not_create_safe_prefix():
    assert not candidate_idr_ranges([],[],0,2)['candidate_clean_ranges']
    r=candidate_idr_ranges([1,3,5],[3],0,6,.08)
    assert r['excluded_prefix']=={'start':0,'end':1} and r['candidate_clean_ranges']==[{'start':5,'end':6}]


@pytest.mark.parametrize('error',[[float('nan')],[-1],[10]])
def test_invalid_error_location_is_not_silently_clamped(error):
    with pytest.raises(ValueError):candidate_idr_ranges([0,2],error,0,4)


def valid_range():return {'start':0.,'end':2.,**{k:True for k in ['primary_strict_passed','independent_strict_passed','original_pts_continuous','frame_count_and_pixels_match','source_identity_bound']}}


def test_admission_distinguishes_strict_degraded_and_unknown():
    args=dict(identity_ok=True,strict_video_ok=True,strict_audio_ok=True,full_timeline_ok=True,cross_decoder_ok=True,validated_ranges=[])
    assert grade(**args)=='A'
    args['strict_video_ok']=False
    assert grade(**args)=='C'
    args['validated_ranges']=[valid_range()]
    assert grade(**args)=='B'
    args['validated_ranges'][0]['frame_count_and_pixels_match']=False
    assert grade(**args)=='C'
    args['validated_ranges']=[valid_range()];args['identity_ok']=False
    assert grade(**args)=='C'
