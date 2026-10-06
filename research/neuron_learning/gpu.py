"""Bounded local Radeon HIP head backend. No CPU fallback for training."""
from __future__ import annotations
import ctypes as C
import hashlib
import json
from pathlib import Path
import numpy as np

INPUTS,HIDDEN,OUTPUTS=32,16,2
PARAMETERS=INPUTS*HIDDEN+HIDDEN+HIDDEN*OUTPUTS+OUTPUTS
HORIZONS=(300,900)
F32=np.float32
LAYERS=((0,528),(528,562))


def initial_weights():
    rng=np.random.default_rng(20261006)
    w=rng.normal(0,0.035,PARAMETERS).astype(F32)
    w[512:528]=0.01
    w[560:562]=0.01
    return w


def numpy_reference(x,y,m,t,tm,w):
    """Numerical oracle only, never selected as a training backend."""
    x,y,m,t,tm,w=[np.asarray(v,dtype=np.float64) for v in (x,y,m,t,tm,w)]
    a=w[:512].reshape(32,16);b=w[512:528]
    c=w[528:560].reshape(16,2);d=w[560:562]
    z=np.tanh(x@a+b);p=z@c+d
    def huber(d):
        return np.where(np.abs(d)<=1,0.5*d*d,np.abs(d)-0.5)
    denom=max(1,float(m.sum()))
    loss=float((m*(huber(p-y)+0.1*tm*huber(p-t))).sum()/denom)
    dp=m*(np.clip(p-y,-1,1)+0.1*tm*np.clip(p-t,-1,1))/denom
    dz=(dp@c.T)*(1-z*z)
    grad=np.concatenate(((x.T@dz).ravel(),dz.sum(axis=0),(z.T@dp).ravel(),dp.sum(axis=0)))
    return p,grad,loss


class GpuHead:
    def __init__(self,path,expected_sha256=None):
        path=Path(path)
        self.sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and self.sha256!=expected_sha256:
            raise ValueError('GPU_BACKEND_HASH_CHANGED')
        self.lib=C.CDLL(str(path))
        ptr=C.POINTER(C.c_float)
        self.lib.hnl_gpu_identity.argtypes=[C.c_char_p,C.c_int]
        self.lib.hnl_gpu_identity.restype=C.c_int
        self.lib.hnl_run.argtypes=[ptr]*6+[C.c_int,C.c_int,C.c_float]+[ptr]*4+[C.c_char_p,C.c_int]
        self.lib.hnl_run.restype=C.c_int
        buf=C.create_string_buffer(1024)
        if self.lib.hnl_gpu_identity(buf,len(buf)):
            raise RuntimeError('GPU_REQUIRED:'+buf.value.decode(errors='replace'))
        self.identity=json.loads(buf.value)
        if self.identity.get('arch')!='gfx1150' or self.identity.get('cpu_fallback') is not False:
            raise RuntimeError('POCKET4_REAL_GPU_REQUIRED')

    def run(self,x,weights,*,y=None,mask=None,teacher=None,teacher_mask=None,epochs=0,lr=0.01):
        x=np.ascontiguousarray(x,dtype=F32)
        if x.ndim!=2 or x.shape[1]!=INPUTS or not 1<=len(x)<=4096:
            raise ValueError('GPU_X_SHAPE')
        n=len(x)
        def target(v):
            result=np.zeros((n,2),dtype=F32) if v is None else np.ascontiguousarray(v,dtype=F32)
            if result.shape!=(n,2):raise ValueError('GPU_TARGET_SHAPE')
            return result
        y,mask,teacher,teacher_mask=[target(v) for v in (y,mask,teacher,teacher_mask)]
        w=np.array(weights,dtype=F32,copy=True,order='C')
        if w.shape!=(PARAMETERS,):raise ValueError('GPU_WEIGHT_SHAPE')
        if type(epochs) is not int:raise ValueError('GPU_EPOCH_TYPE')
        pred=np.zeros((n,2),dtype=F32);grad=np.zeros(PARAMETERS,dtype=F32)
        loss=C.c_float();elapsed=C.c_float();error=C.create_string_buffer(1024)
        ptr=lambda a:a.ctypes.data_as(C.POINTER(C.c_float))
        rc=self.lib.hnl_run(*[ptr(a) for a in (x,y,mask,teacher,teacher_mask,w)],n,epochs,lr,
                            ptr(pred),ptr(grad),C.byref(loss),C.byref(elapsed),error,len(error))
        if rc:raise RuntimeError(error.value.decode(errors='replace'))
        if not all(np.isfinite(a).all() for a in (w,pred,grad)):
            raise RuntimeError('GPU_NONFINITE_RESULT')
        return {'weights':w,'prediction':pred,'gradient':grad,'loss':loss.value,
                'gpu_kernel_ms':elapsed.value,'backend':self.identity}


def project_candidate(baseline,candidate):
    """Conservative precursor to the mandatory native Q32 norm verification.

    The native Plasticity owner primitive independently checks exact integer norms.
    These are candidate-relative bounds, never a permission to modify live weights.
    """
    b=np.asarray(baseline,dtype=np.float64);c=np.asarray(candidate,dtype=np.float64)
    if b.shape!=(562,) or c.shape!=b.shape or not np.isfinite(b).all() or not np.isfinite(c).all():
        raise ValueError('PLASTICITY_PARAMETER_SHAPE')
    delta=c-b
    for start,end in LAYERS:
        norm=float(np.linalg.norm(b[start:end]));dn=float(np.linalg.norm(delta[start:end]))
        if norm==0:raise ValueError('PLASTICITY_ZERO_LAYER_DENOMINATOR')
        if dn>0.0048*norm:delta[start:end]*=(0.0048*norm/dn)
    norm=float(np.linalg.norm(b));dn=float(np.linalg.norm(delta))
    if dn>0.0024*norm:delta*=0.0024*norm/dn
    # The stored candidate is exactly the returned Q32 quantization projected to F32.
    result=(b+delta).astype(F32)
    return result


def numerical_self_test(backend):
    rng=np.random.default_rng(731)
    x=rng.normal(0,.2,(17,32)).astype(F32);y=rng.normal(0,.1,(17,2)).astype(F32)
    mask=np.ones_like(y);mask[0,1]=0
    t=y*.8;tm=np.zeros_like(y);tm[::3]=1
    w=initial_weights();gpu=backend.run(x,w,y=y,mask=mask,teacher=t,teacher_mask=tm)
    pred,grad,loss=numpy_reference(x,y,mask,t,tm,w)
    np.testing.assert_allclose(gpu['prediction'],pred,rtol=2e-5,atol=2e-6)
    np.testing.assert_allclose(gpu['gradient'],grad,rtol=2e-4,atol=2e-6)
    assert abs(gpu['loss']-loss)<2e-6
    for k in (0,139,512,529,561):
        a=w.astype(np.float64);b=a.copy();a[k]+=1e-5;b[k]-=1e-5
        fd=(numpy_reference(x,y,mask,t,tm,a)[2]-numpy_reference(x,y,mask,t,tm,b)[2])/2e-5
        assert abs(fd-grad[k])<1e-6
    after=backend.run(x,w,y=y,mask=mask,teacher=t,teacher_mask=tm,epochs=16)
    assert after['loss']<gpu['loss']
    try:backend.run(x,w,epochs=1)
    except RuntimeError as e:assert 'NO_MATURE_LABEL' in str(e)
    else:raise AssertionError('training without labels must fail')
    return {'schema':'heptatrader.gpu-head-numerical-test.v1','fixture_kind':'SYNTHETIC_NUMERICAL_TEST_ONLY',
            'device':backend.identity,'loss_before':gpu['loss'],'loss_after':after['loss'],
            'forward_max_abs_error':float(np.max(np.abs(gpu['prediction']-pred))),
            'gradient_max_abs_error':float(np.max(np.abs(gpu['gradient']-grad))),
            'gpu_kernel_ms':after['gpu_kernel_ms'],'changed_parameters':int(np.count_nonzero(w!=after['weights'])),
            'finite_difference_passed':True,'no_label_no_update_passed':True,
            'broker_orders_sent':0,'real_strategy_acceptance':False}
