"""Small read-only intake rules. No decoder, model, data path or production changes.

Clean ranges are candidates until their original compressed packets have passed
independent strict decoding and their original timestamps have been checked.
"""
from bisect import bisect_right
import math
import statistics


def timestamp_summary(rows,frame_seconds):
    """Summarize original timestamps; never interpolate or fill missing frames."""
    if not math.isfinite(frame_seconds) or frame_seconds<=0:raise ValueError('Positive frame duration required')
    first=last=None;count=missing=non_increasing=0;gaps=[];deltas=[]
    for value in rows:
        count+=1
        if value is None or not math.isfinite(value):missing+=1;continue
        if first is None:first=value
        if last is not None:
            delta=value-last;deltas.append(delta)
            if delta<=0:non_increasing+=1
            elif delta>frame_seconds*1.5:gaps.append({'previous_pts':last,'next_pts':value,'gap_seconds':delta-frame_seconds})
        last=value
    return {'frames':count,'missing_pts':missing,'non_increasing':non_increasing,'first_pts':first,'last_pts':last,
        'median_step_seconds':statistics.median(deltas) if deltas else None,'maximum_step_seconds':max(deltas) if deltas else None,
        'gaps':gaps,'uncovered_seconds':sum(g['gap_seconds'] for g in gaps),
        'timeline_continuous':count>0 and missing==0 and non_increasing==0 and not gaps}


def candidate_idr_ranges(idr_times,error_times,start,end,guard_seconds=0.):
    """Exclude entire IDR-bounded GOPs touched by errors/reordering guards.

No IDR before the error means the prefix remains unusable. Time origins are
external and must be recorded, never inferred from a filename or wall clock.
"""
    values=[*idr_times,*error_times,start,end,guard_seconds]
    if not all(math.isfinite(x) for x in values) or end<=start or guard_seconds<0:raise ValueError('Invalid range/PTS')
    keys=list(idr_times)
    if keys!=sorted(set(keys)) or any(k<start or k>=end for k in keys):raise ValueError('Ordered unique in-range IDRs required')
    if any(t<start or t>=end for t in error_times):raise ValueError('Error timestamp outside source support')
    intervals=[(t,keys[i+1] if i+1<len(keys) else end) for i,t in enumerate(keys)]
    bad=set()
    for e in error_times:
        lo=max(start,e-guard_seconds);hi=min(end,e+guard_seconds)
        for i,(a,b) in enumerate(intervals):
            if a<=hi+1e-7 and b>lo+1e-7:bad.add(i)
    good=[v for i,v in enumerate(intervals) if i not in bad]
    merged=[]
    for a,b in good:
        if merged and abs(merged[-1]['end']-a)<1e-7:merged[-1]['end']=b
        else:merged.append({'start':a,'end':b})
    return {'candidate_clean_ranges':merged,'excluded_gop_indices':sorted(bad),'excluded_prefix':None if keys and keys[0]==start else {'start':start,'end':keys[0] if keys else end},
        'strictly_validated':False,'note':'Candidate mask only; not proof of content or usable intervals.'}


def grade(*,identity_ok,strict_video_ok,strict_audio_ok,full_timeline_ok,cross_decoder_ok,validated_ranges):
    """No B admission based only on playback, guessed masks or progress=end."""
    if not identity_ok:return 'C'
    if strict_video_ok and strict_audio_ok and full_timeline_ok and cross_decoder_ok:return 'A'
    for r in validated_ranges:
        if not all(r.get(k) is True for k in ['primary_strict_passed','independent_strict_passed','original_pts_continuous','frame_count_and_pixels_match','source_identity_bound']):return 'C'
        if not math.isfinite(r['start']) or not math.isfinite(r['end']) or r['end']<=r['start']:return 'C'
    return 'B' if validated_ranges and strict_audio_ok else 'C'
