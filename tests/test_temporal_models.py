from types import SimpleNamespace
import numpy as np
from packages.temporal.models import tcn_initialize,tcn_forward,tcn_loss_gradient,tcn_fit,convolution


def test_tcn_gradients_match_finite_differences():
    rng=np.random.default_rng(4);x=rng.normal(size=(2,5,2));y=rng.normal(size=(2,4))
    p=tcn_initialize(2,3,4,7);_,g=tcn_loss_gradient(x,y,p)
    for key,indices in {'w1':[(0,0,0),(2,1,2)],'b1':[(1,)],'w2':[(0,1,0),(2,2,1)],'b2':[(1,)],'wo':[(2,3)],'bo':[(0,)]}.items():
        for index in indices:
            value=p[key][index];epsilon=1e-6
            p[key][index]=value+epsilon;plus=tcn_loss_gradient(x,y,p)[0]
            p[key][index]=value-epsilon;minus=tcn_loss_gradient(x,y,p)[0]
            p[key][index]=value
            np.testing.assert_allclose(g[key][index],(plus-minus)/(2*epsilon),rtol=1e-4,atol=1e-6)


def test_causal_convolution_does_not_read_future_and_training_changes_weights():
    rng=np.random.default_rng(5);x=rng.normal(size=(20,8,2));p=tcn_initialize(2,4,2,8)
    y=convolution(x,p['w1'],p['b1'],2)
    changed=x.copy();changed[:,5:]+=1000
    np.testing.assert_array_equal(y[:,:5],convolution(changed,p['w1'],p['b1'],2)[:,:5])
    learning=SimpleNamespace(hidden_dim=4,epochs=20,batch_size=10,learning_rate=.02)
    target=x[:,-1]*.5
    fitted,log=tcn_fit(x,target,learning,8)
    assert log['parameter_delta_l2']>0
    assert log['epoch_losses'][-1]<log['initial_loss']
    repeat,_=tcn_fit(x,target,learning,8)
    for key in fitted:np.testing.assert_array_equal(fitted[key],repeat[key])
