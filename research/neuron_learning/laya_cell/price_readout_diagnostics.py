"""Fixed CPU diagnostics of a frozen seven-class readout; no model promotion."""
import numpy as np

K=7
FLOOR=1e-30

def log_softmax(z):
    z=np.asarray(z,dtype=np.float64)
    if z.ndim!=2 or not np.isfinite(z).all(): raise ValueError("finite matrix required")
    a=z-z.max(axis=1,keepdims=True)
    return a-np.log(np.exp(a).sum(axis=1,keepdims=True))

def labels(values,n):
    y=np.asarray(values)
    if y.shape!=(n,) or n<1 or y.dtype.kind not in "iu":
        raise ValueError("integer labels matching nonempty rows required")
    y=y.astype(np.int64,copy=True)
    if np.any(y<0) or np.any(y>=K): raise ValueError("class outside 0..6")
    return y

def validated(probs,truth):
    p=np.array(probs,dtype=np.float64,copy=True)
    if p.ndim!=2 or p.shape[1]!=K or not len(p): raise ValueError("N by 7 probabilities required")
    if not np.isfinite(p).all() or np.any(p<0) or np.any(p>1): raise ValueError("invalid probabilities")
    y=labels(truth,len(p));err=float(np.abs(p.sum(1)-1).max())
    if err>1e-5: raise ValueError("probability sum")
    return log_softmax(np.log(np.maximum(p,FLOOR))),y,{"input_probability_floor":FLOOR,
        "input_floored_values":int(np.count_nonzero(p<FLOOR)),"input_max_row_sum_error":err}

def metrics(probs,truth,*,bias=None,inverse_temperature=None):
    z,y,notes=validated(probs,truth)
    if bias is not None and inverse_temperature is not None: raise ValueError("separate arms required")
    if bias is not None:
        b=np.asarray(bias,dtype=np.float64)
        if b.shape!=(K,) or not np.isfinite(b).all(): raise ValueError("invalid bias")
        z=z+b
    if inverse_temperature is not None:
        u=float(inverse_temperature)
        if not np.isfinite(u) or u<0: raise ValueError("invalid inverse temperature")
        z=z*u
    lq=log_softmax(z);q=np.exp(lq);pred=q.argmax(1);n=len(y);true_lq=lq[np.arange(n),y]
    cm=np.zeros((K,K),dtype=np.int64);np.add.at(cm,(y,pred),1)
    return {"rows":n,"nll":float(-np.maximum(true_lq,np.log(FLOOR)).mean()),
        "uncapped_logspace_nll":float(-true_lq.mean()),"nll_probability_floor":FLOOR,
        "nll_floor_hits":int(np.count_nonzero(true_lq<np.log(FLOOR))),
        "brier":float(np.square(q-np.eye(K)[y]).sum(1).mean()),
        "accuracy":float(np.mean(pred==y)),"true_class_counts":np.bincount(y,minlength=K).tolist(),
        "predicted_class_counts":np.bincount(pred,minlength=K).tolist(),
        "mean_probability_by_class":q.mean(0).tolist(),"confusion":cm.tolist(),
        "argmax_tied_rows":int(np.count_nonzero((q==q.max(1,keepdims=True)).sum(1)>1)),**notes}

def fit_frequency(train_labels):
    y=labels(train_labels,len(train_labels));c=np.bincount(y,minlength=K)
    return {"train_rows":len(y),"training_class_counts":c.tolist(),"smoothing_per_class":.5,
            "probabilities":((c+.5)/(len(y)+K*.5)).tolist()}

def bias_eval(logp,y,b,lam):
    lq=log_softmax(logp+b);q=np.exp(lq);n=len(y)
    objective=float(-lq[np.arange(n),y].mean()+.5*lam*np.dot(b,b))
    g=q.mean(0)-np.bincount(y,minlength=K)/float(n)+lam*b
    h=np.diag(q.mean(0))-(q.T@q)/float(n)+lam*np.eye(K)
    return objective,g,h

def fit_bias(train_probs,train_labels):
    logp,y,notes=validated(train_probs,train_labels);lam=1./len(y);b=np.zeros(K)
    initial=bias_eval(logp,y,b,lam)[0];trace=[initial]
    for _ in range(50):
        value,g,h=bias_eval(logp,y,b,lam)
        if np.abs(g).max()<=1e-8: break
        step=np.linalg.solve(h,g);descent=float(g@step)
        if not np.isfinite(step).all() or descent<=0: raise RuntimeError("invalid Newton direction")
        eta=1.
        for _ in range(30):
            trial=b-eta*step;v=bias_eval(logp,y,trial,lam)[0]
            if v<=value-1e-4*eta*descent:
                b=trial;trace.append(v);break
            eta*=.5
        else: raise RuntimeError("Armijo failed; no fallback")
    final,g,_=bias_eval(logp,y,b,lam);norm=float(np.abs(g).max())
    if not np.isfinite(final) or norm>1e-8: raise RuntimeError("fixed bias budget did not converge")
    return {"state":"CONVERGED","train_rows":len(y),"lambda":lam,"bias":b.tolist(),
        "bias_sum":float(b.sum()),"applied_newton_steps":len(trace)-1,"gradient_inf":norm,
        "train_objective_initial":initial,"train_objective_final":final,
        "accepted_objective_trace":trace,"train_metrics":metrics(train_probs,train_labels,bias=b),**notes}

def fit_temperature(train_probs,train_labels):
    logp,y,notes=validated(train_probs,train_labels);lam=1./len(y)
    def evaluate(u):
        lq=log_softmax(u*logp);q=np.exp(lq)
        return (float(-lq[np.arange(len(y)),y].mean()+.5*lam*(u-1)**2),
                float((q*logp).sum(1).mean()-logp[np.arange(len(y)),y].mean()+lam*(u-1)))
    lo,hi=0.,10.;steps=0
    if evaluate(lo)[1]>=0: u,boundary=lo,"LOWER_UNIFORM"
    elif evaluate(hi)[1]<=0: u,boundary=hi,"UPPER"
    else:
        boundary=None
        for _ in range(80):
            mid=.5*(lo+hi)
            if evaluate(mid)[1]>0: hi=mid
            else: lo=mid
            steps+=1
            if hi-lo<=1e-10: break
        u=.5*(lo+hi)
    final,g=evaluate(u)
    kkt=max(0.,-g) if boundary=="LOWER_UNIFORM" else max(0.,g) if boundary=="UPPER" else abs(g)
    if not np.isfinite(final) or kkt>1e-7: raise RuntimeError("fixed temperature KKT failed")
    return {"state":"CONVERGED","train_rows":len(y),"lambda":lam,"inverse_temperature":u,
        "temperature":None if u==0 else 1/u,"fixed_bounds":[0.,10.],"boundary":boundary,
        "uniform_degenerate":u==0,"bisection_steps":steps,"gradient":g,"kkt_residual":kkt,
        "train_objective_initial":evaluate(1.)[0],"train_objective_final":final,
        "train_metrics":metrics(train_probs,train_labels,inverse_temperature=u),**notes}
