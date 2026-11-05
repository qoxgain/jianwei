"""Bounded deterministic block matching, not a learned optical-flow model."""
import numpy as np
from packages.config.errors import ContractError


def crop(frames,box):
    h,w=frames.shape[1:3];x1,y1,x2,y2=box
    if not 0<=x1<x2<=1 or not 0<=y1<y2<=1:raise ContractError('MOTION_ROI_INVALID','ROI为空或超出显示画面')
    left,top=int(x1*w),int(y1*h);right,bottom=int(np.ceil(x2*w)),int(np.ceil(y2*h))
    if right-left<2 or bottom-top<2:raise ContractError('MOTION_ROI_EMPTY','ROI有效像素不足')
    return frames[:,top:bottom,left:right]


def gray_resize(frames,size):
    # Nearest sampling is explicit in the B.1 recipe, separate from R3D resize.
    h,w=frames.shape[1:3];ys=np.minimum((np.arange(size)+.5)*h/size,h-1).astype(int);xs=np.minimum((np.arange(size)+.5)*w/size,w-1).astype(int)
    return (frames[:,ys[:,None],xs[None,:]].astype(np.float32)/255).mean(axis=-1)


def descriptors(frames,pts,settings):
    gray=gray_resize(frames,settings.motion_size);dt=np.diff(pts)/1000
    if not len(dt) or np.any(dt<=0):raise ContractError('MOTION_PTS_INVALID','连续样本PTS必须严格递增')
    difference=float(np.mean(np.abs(np.diff(gray,axis=0)).mean(axis=(1,2))/dt))
    radius= settings.search_radius;b=settings.block_size;size=settings.motion_size
    offsets=sorted([(dx,dy) for dy in range(-radius,radius+1) for dx in range(-radius,radius+1)],key=lambda v:(v[0]*v[0]+v[1]*v[1],v))
    vectors=[];textured=0;total=0
    for k in range(len(gray)-1):
        previous,current=gray[k],np.pad(gray[k+1],radius,mode='edge')
        for y in range(0,size,b):
            for x in range(0,size,b):
                total+=1;block=previous[y:y+b,x:x+b]
                if float(block.std())<settings.texture_floor:continue
                textured+=1
                costs=np.array([np.mean((block-current[y+radius+dy:y+radius+dy+b,x+radius+dx:x+radius+dx+b])**2) for dx,dy in offsets])
                best=int(costs.argmin());zero=float(costs[0]);dx,dy=offsets[best]
                if zero<=1e-12 or zero-float(costs[best])<settings.motion_improvement*zero:dx=dy=0
                vectors.append((dx/size/dt[k],dy/size/dt[k]))
    a=np.asarray(vectors,dtype=np.float64).reshape(-1,2);magnitude=np.linalg.norm(a,axis=1)
    return {'frame_difference':difference,'block_motion':float(magnitude.mean()) if len(a) else 0.,
        'motion_p90':float(np.quantile(magnitude,.9)) if len(a) else 0.,'textured_fraction':textured/max(1,total),
        'moving_fraction':float(np.mean(magnitude>0)) if len(a) else 0.}
