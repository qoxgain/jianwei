"""Deterministic NumPy baselines and a trainable two-layer causal convolution.

All fit functions accept training arrays only. No visual encoder is modified.
"""
import numpy as np


def fit_projection(rows, dimension):
    mean=rows.mean(axis=0); scale=rows.std(axis=0)
    scale=np.where(scale>1e-6,scale,1.0)
    z=(rows-mean)/scale
    _, singular, vt=np.linalg.svd(z,full_matrices=False)
    rank=min(dimension,len(vt),max(1,int(np.count_nonzero(singular>1e-8))))
    components=vt[:rank].T.copy()
    for i in range(rank):
        if components[np.argmax(abs(components[:,i])),i]<0: components[:,i]*=-1
    explained=float(np.square(singular[:rank]).sum()/max(np.square(singular).sum(),1e-12))
    return {'mean':mean,'scale':scale,'components':components},explained


def project(x,p): return ((x-p['mean'])/p['scale'])@p['components']
def reconstruct(x,p): return (x@p['components'].T)*p['scale']+p['mean']


def kmeans(x,k,iterations,seed):
    rng=np.random.default_rng(seed); centers=[x[rng.integers(len(x))].copy()]
    for _ in range(k-1):
        distance=np.min(((x[:,None]-np.array(centers)[None])**2).sum(-1),axis=1)
        centers.append(x[int(np.argmax(distance))].copy())
    centers=np.array(centers)
    for _ in range(iterations):
        ids=assign(x,centers)
        updated=np.array([x[ids==i].mean(0) if np.any(ids==i) else centers[i] for i in range(k)])
        if np.allclose(updated,centers,atol=1e-10,rtol=0): break
        centers=updated
    return centers


def assign(x,centers): return np.argmin(((x[...,None,:]-centers)**2).sum(-1),axis=-1)


def ridge_fit(x,y,regularization):
    x=x.reshape(len(x),-1); y=y.reshape(len(y),-1)
    xm,ym=x.mean(0),y.mean(0); xc,yc=x-xm,y-ym
    if xc.shape[1]<=len(xc):
        w=np.linalg.solve(xc.T@xc+regularization*np.eye(xc.shape[1]),xc.T@yc)
    else:
        w=xc.T@np.linalg.solve(xc@xc.T+regularization*np.eye(len(xc)),yc)
    return {'w':w,'b':ym-xm@w}


def ridge_predict(x,parameters): return x.reshape(len(x),-1)@parameters['w']+parameters['b']


def shifted(x,offset):
    if not offset:return x
    result=np.zeros_like(x)
    if offset<x.shape[1]:result[:,offset:]=x[:,:-offset]
    return result


def convolution(x,w,b,dilation):
    return sum(shifted(x,i*dilation)@w[i] for i in range(len(w)))+b


def conv_backward(x,w,grad,dilation):
    dx=np.zeros_like(x);dw=np.zeros_like(w)
    for i in range(len(w)):
        offset=i*dilation
        dw[i]=np.einsum('nti,nto->io',shifted(x,offset),grad)
        part=grad@w[i].T
        if not offset:dx+=part
        elif offset<x.shape[1]:dx[:,:-offset]+=part[:,offset:]
    return dx,dw,grad.sum(axis=(0,1))


def tcn_initialize(dimension,hidden,outputs,seed):
    rng=np.random.default_rng(seed)
    return {'w1':rng.normal(0,1/np.sqrt(3*dimension),(3,dimension,hidden)), 'b1':np.zeros(hidden),
            'w2':rng.normal(0,1/np.sqrt(3*hidden),(3,hidden,hidden)), 'b2':np.zeros(hidden),
            'wo':rng.normal(0,1/np.sqrt(hidden),(hidden,outputs)), 'bo':np.zeros(outputs)}


def tcn_forward(x,p,return_cache=False):
    a=np.tanh(convolution(x,p['w1'],p['b1'],1))
    b=np.tanh(convolution(a,p['w2'],p['b2'],2))
    y=b[:,-1]@p['wo']+p['bo']
    return (y,(a,b)) if return_cache else y


def tcn_loss_gradient(x,y,p):
    prediction,(a,b)=tcn_forward(x,p,True);difference=prediction-y
    loss=float(np.mean(difference**2)); grad=2*difference/difference.size
    gradients={'wo':b[:,-1].T@grad,'bo':grad.sum(0)}
    db=np.zeros_like(b);db[:,-1]=grad@p['wo'].T
    da,gradients['w2'],gradients['b2']=conv_backward(a,p['w2'],db*(1-b*b),2)
    _,gradients['w1'],gradients['b1']=conv_backward(x,p['w1'],da*(1-a*a),1)
    return loss,gradients


def tcn_fit(x,y,learning,seed,clip_norm=5.0):
    y=y.reshape(len(y),-1)
    p=tcn_initialize(x.shape[-1],learning.hidden_dim,y.shape[-1],seed)
    original={k:v.copy() for k,v in p.items()}
    first=tcn_loss_gradient(x,y,p)[0]
    m={k:np.zeros_like(v) for k,v in p.items()};v={k:a.copy() for k,a in m.items()}
    rng=np.random.default_rng(seed);step=0;losses=[]
    for _ in range(learning.epochs):
        order=rng.permutation(len(x))
        for begin in range(0,len(order),learning.batch_size):
            ids=order[begin:begin+learning.batch_size]; _,g=tcn_loss_gradient(x[ids],y[ids],p)
            norm=np.sqrt(sum(np.sum(a*a) for a in g.values()));factor=min(1.0,clip_norm/max(norm,1e-12));step+=1
            for key in p:
                grad=g[key]*factor;m[key]=.9*m[key]+.1*grad;v[key]=.999*v[key]+.001*grad*grad
                p[key]-=learning.learning_rate*(m[key]/(1-.9**step))/(np.sqrt(v[key]/(1-.999**step))+1e-8)
        losses.append(float(np.mean((tcn_forward(x,p)-y)**2)))
    return p,{'initial_loss':first,'epoch_losses':losses,
              'parameter_delta_l2':float(np.sqrt(sum(np.sum((p[k]-original[k])**2) for k in p))),
              'architecture':'two causal tanh convolutions, kernel=3, dilations=[1,2]; last-time readout',
              'receptive_field_windows':7,'optimizer':'Adam','seed':seed}
