"""Small causal heads for retrospective ablations; frozen visual features only."""
import numpy as np
from packages.temporal.models import convolution,conv_backward


def initialize(dimension,hidden,outputs,seed,dilations,zero_output=False):
    rng=np.random.default_rng(seed);params={};previous=dimension
    for layer,_ in enumerate(dilations):
        params[f'w{layer}']=rng.normal(0,1/np.sqrt(3*previous),(3,previous,hidden))
        params[f'b{layer}']=np.zeros(hidden);previous=hidden
    params['out_w']=rng.normal(0,1/np.sqrt(hidden),(hidden,outputs))
    if zero_output:params['out_w'][:]=0
    params['out_b']=np.zeros(outputs)
    return params


def forward(x,p,dilations,cache=False):
    activations=[x]
    for layer,dilation in enumerate(dilations):
        activations.append(np.tanh(convolution(activations[-1],p[f'w{layer}'],p[f'b{layer}'],dilation)))
    prediction=activations[-1][:,-1]@p['out_w']+p['out_b']
    return (prediction,activations) if cache else prediction


def loss_gradient(x,y,p,dilations):
    prediction,activations=forward(x,p,dilations,True);delta=prediction-y
    grad=2*delta/delta.size
    gradients={'out_w':activations[-1][:,-1].T@grad,'out_b':grad.sum(0)}
    da=np.zeros_like(activations[-1]);da[:,-1]=grad@p['out_w'].T
    for layer in reversed(range(len(dilations))):
        da,dw,db=conv_backward(activations[layer],p[f'w{layer}'],da*(1-activations[layer+1]**2),dilations[layer])
        gradients[f'w{layer}'],gradients[f'b{layer}']=dw,db
    return float(np.mean(delta**2)),gradients


def fit(x,y,learning,seed,dilations,clip_norm,*,score=None,train_score=None,select_validation=False,zero_output=False):
    """score sees validation only; no test/holdout parameter exists here."""
    y=y.reshape(len(y),-1)
    p=initialize(x.shape[-1],learning.hidden_dim,y.shape[-1],seed,dilations,zero_output)
    initial={k:v.copy() for k,v in p.items()}
    first=loss_gradient(x,y,p,dilations)[0]
    best={k:v.copy() for k,v in p.items()};best_score=score(p) if score else None;best_epoch=0
    raw_train_losses=[float(train_score(p))] if train_score else []
    train_losses=[first];val_losses=[best_score] if score else []
    m={k:np.zeros_like(v) for k,v in p.items()};variance={k:v.copy() for k,v in m.items()}
    rng=np.random.default_rng(seed);step=0
    for epoch in range(1,learning.epochs+1):
        order=rng.permutation(len(x))
        for begin in range(0,len(order),learning.batch_size):
            ids=order[begin:begin+learning.batch_size];_,gradients=loss_gradient(x[ids],y[ids],p,dilations)
            norm=np.sqrt(sum(np.sum(g*g) for g in gradients.values()));factor=min(1.,clip_norm/max(norm,1e-12));step+=1
            for key in p:
                g=gradients[key]*factor;m[key]=.9*m[key]+.1*g;variance[key]=.999*variance[key]+.001*g*g
                p[key]-=learning.learning_rate*(m[key]/(1-.9**step))/(np.sqrt(variance[key]/(1-.999**step))+1e-8)
        loss=loss_gradient(x,y,p,dilations)[0]
        if not np.isfinite(loss) or any(not np.isfinite(v).all() for v in p.values()):
            raise ValueError('Nonfinite exploratory head; no alternate seed or relaxed policy')
        train_losses.append(loss)
        if train_score:raw_train_losses.append(float(train_score(p)))
        if score:
            value=float(score(p))
            if not np.isfinite(value):raise ValueError('Nonfinite validation score')
            val_losses.append(value)
            if value<best_score:
                best_score=value;best_epoch=epoch;best={k:v.copy() for k,v in p.items()}
    selected=best if select_validation else p
    return selected,{'train_latent_mse':train_losses,'train_raw_feature_mse':raw_train_losses,
        'final_parameter_delta_l2':float(np.sqrt(sum(np.sum((p[k]-initial[k])**2) for k in p))),'validation_raw_feature_mse':val_losses,
        'selected_epoch':best_epoch if select_validation else learning.epochs,
        'selection_split':'validation' if select_validation else 'fixed_final_epoch',
        'parameter_delta_l2':float(np.sqrt(sum(np.sum((selected[k]-initial[k])**2) for k in selected))),
        'dilations':list(dilations),'receptive_field_windows':1+2*sum(dilations),
        'configured_history_windows':x.shape[1],'effective_history_windows':min(x.shape[1],1+2*sum(dilations)),
        'seed':seed,'encoder_updated':False,'holdout_used_for_selection':False}


def delta_input(z,mode,seed):
    if mode=='direct':return z
    if mode=='direct_last_only':return np.repeat(z[:,-1:,:],z.shape[1],axis=1)
    centered=z-z[:,-1:,:]
    if mode=='delta_last_only':return np.zeros_like(centered)
    if mode=='delta_shuffled_history':
        result=centered.copy();rng=np.random.default_rng(seed)
        for row in result:row[:-1]=row[rng.permutation(len(row)-1)]
        return result
    if mode=='delta_velocity':
        return np.diff(z,axis=1,prepend=z[:,:1])
    if mode=='delta':return centered
    raise ValueError('Unknown exploratory input transformation')


def decode(prediction,projection,last,horizons,residual):
    latent=prediction.reshape(len(last),horizons,-1)
    raw=(latent@projection['components'].T)*projection['scale']
    return raw+last[:,None,:] if residual else raw+projection['mean']
