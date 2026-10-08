"""Causal probabilistic and executable-side value readouts; no trading API.
NumPy only. All normalization/model parameters use the explicit fit population.
"""
from __future__ import annotations
import math
import numpy as np
from .common import EvidenceError


def features(rows, profile='compact'):
    out=[]
    for row in rows:
        r=[float(x) for x in row['features']]
        if len(r)!=25 or not all(math.isfinite(x) for x in r):
            raise EvidenceError('FEATURE_SHAPE_OR_NONFINITE')
        if profile=='raw25':out.append(r);continue
        if profile!='compact':raise EvidenceError('FEATURE_PROFILE')
        closes=r[:11]+[0.0];steps=np.diff(closes)
        vol=float(np.sqrt(np.mean(steps*steps)))
        spreads=r[11:23]
        if min(spreads)<0:raise EvidenceError('NEGATIVE_HISTORICAL_SPREAD')
        out.append([-closes[-2],-closes[-4],-closes[-7],-closes[0],
                    math.log1p(vol),float(np.mean(np.abs(steps))),
                    float(np.max(np.abs(steps))),float(np.mean(steps>0)),
                    spreads[-1],float(np.mean(spreads)),max(spreads),r[23],r[24]])
    result=np.asarray(out,dtype=np.float64)
    if result.ndim!=2 or not len(result):raise EvidenceError('EMPTY_FEATURES')
    return result


def checked_labels(labels):
    raw=np.asarray(labels)
    if raw.ndim!=1 or not len(raw) or raw.dtype.kind not in 'iu' or not np.isin(raw,[0,1,2]).all():
        raise EvidenceError('LABEL_CLASS')
    return raw.astype(int)


def prior(labels):
    y=checked_labels(labels);counts=np.bincount(y,minlength=3).astype(float)
    if np.min(counts)<1:raise EvidenceError('MISSING_TRAIN_CLASS')
    return counts/counts.sum()


def softmax(logits):
    z=np.asarray(logits,dtype=np.float64)
    if z.ndim!=2 or z.shape[1]!=3 or not np.isfinite(z).all():raise EvidenceError('LOGIT_SHAPE_OR_NONFINITE')
    e=np.exp(z-z.max(axis=1,keepdims=True));return e/e.sum(axis=1,keepdims=True)


def check_probabilities(p):
    p=np.asarray(p,dtype=np.float64)
    if p.ndim!=2 or p.shape[1]!=3 or not len(p) or not np.isfinite(p).all() or np.min(p)<0 or not np.allclose(p.sum(1),1,atol=1e-10,rtol=0):
        raise EvidenceError('PROBABILITY_SHAPE_OR_SIMPLEX')
    return p


def brier(p,y):
    p=check_probabilities(p);y=checked_labels(y)
    if len(p)!=len(y):raise EvidenceError('LABEL_LENGTH')
    return np.sum((p-np.eye(3)[y])**2,axis=1)


def logloss(p,y):
    p=check_probabilities(p);y=checked_labels(y)
    if len(p)!=len(y):raise EvidenceError('LABEL_LENGTH')
    return float(-np.log(np.maximum(p[np.arange(len(y)),y],1e-15)).mean())


def normalize_fit(x):
    x=np.asarray(x,dtype=np.float64)
    if x.ndim!=2 or len(x)<3 or not np.isfinite(x).all():raise EvidenceError('FIT_FEATURES')
    mean=x.mean(0);std=x.std(0);std[std<1e-6]=1
    return mean,std


def design(x,mean,std):
    x=np.asarray(x,dtype=np.float64);mean=np.asarray(mean);std=np.asarray(std)
    if x.ndim!=2 or mean.shape!=(x.shape[1],) or std.shape!=mean.shape or np.min(std)<=0 or not np.isfinite(x).all():
        raise EvidenceError('NORMALIZER_SHAPE')
    return np.column_stack((np.clip((x-mean)/std,-8,8),np.ones(len(x))))


def logistic_objective(a,y,w,penalty):
    # The third class is the reference logit, removing softmax gauge ambiguity.
    p=softmax(np.column_stack((a@w,np.zeros(len(a)))))
    objective=logloss(p,y)+penalty*.5*float(np.sum(w[:-1]**2))
    gradient=a.T@(p[:,:2]-np.eye(3)[y,:2])/len(a)
    gradient[:-1]+=penalty*w[:-1]
    return objective,gradient,p


def fit_logistic(x,y,penalty):
    if not math.isfinite(penalty) or penalty<=0:raise EvidenceError('PENALTY')
    y=checked_labels(y);mean,std=normalize_fit(x);a=design(x,mean,std)
    if len(a)!=len(y):raise EvidenceError('LABEL_LENGTH')
    base=prior(y);w=np.zeros((a.shape[1],2));w[-1]=np.log(base[:2]/base[2])
    d=a.shape[1];ridge=np.eye(d)*penalty;ridge[-1,-1]=1e-10
    losses=[];converged=False
    for iteration in range(45):
        obj,g,p=logistic_objective(a,y,w,penalty);losses.append(obj)
        if np.max(np.abs(g))<1e-7:converged=True;break
        blocks=[]
        for k in range(2):
            row=[]
            for j in range(2):
                weight=p[:,k]*((1.0 if k==j else 0.0)-p[:,j])
                block=a.T@(a*weight[:,None])/len(a)
                if k==j:block=block+ridge
                row.append(block)
            blocks.append(row)
        h=np.block(blocks)
        delta=np.linalg.solve(h,g.T.reshape(-1)).reshape(2,d).T
        descent=float(np.sum(g*delta));rate=1.0
        for _ in range(25):
            candidate=w-rate*delta;next_obj,_,_=logistic_objective(a,y,candidate,penalty)
            if next_obj<=obj-1e-4*rate*descent:break
            rate*=.5
        else:raise EvidenceError('LOGISTIC_LINE_SEARCH')
        w=candidate
    obj,g,_=logistic_objective(a,y,w,penalty)
    if np.max(np.abs(g))<1e-6:converged=True
    if not converged:raise EvidenceError('LOGISTIC_NOT_CONVERGED')
    return {'family':'softmax','mean':mean.tolist(),'std':std.tolist(),'weights':w.tolist(),
            'penalty':penalty,'fit_rows':len(y),'fit_prior':base.tolist(),
            'gradient_inf':float(np.max(np.abs(g))),'iterations':iteration+1,'fit_objective':obj,
            'objective_monotone':all(b<=a+1e-10 for a,b in zip(losses,losses[1:]))}


def fit_volatility(rows,y,bins=5):
    y=checked_labels(y);x=features(rows,'compact')[:,4]
    boundaries=np.unique(np.quantile(x,np.arange(1,bins)/bins))
    group=np.searchsorted(boundaries,x,side='right');base=prior(y);probs=[]
    for i in range(len(boundaries)+1):
        counts=np.bincount(y[group==i],minlength=3)
        probs.append(((counts+30*base)/(counts.sum()+30)).tolist())
    return {'family':'volatility_prior','boundaries':boundaries.tolist(),'probabilities':probs,
            'fit_prior':base.tolist(),'fit_rows':len(y),'shrinkage_rows':30}


def predict(model,rows):
    if model['family']=='softmax':
        a=design(features(rows,model['profile']),model['mean'],model['std'])
        return softmax(np.column_stack((a@np.asarray(model['weights']),np.zeros(len(rows)))))
    if model['family']=='volatility_prior':
        group=np.searchsorted(model['boundaries'],features(rows,'compact')[:,4],side='right')
        return check_probabilities(np.asarray(model['probabilities'])[group])
    if model['family']=='constant_prior':return np.tile(model['fit_prior'],(len(rows),1))
    raise EvidenceError('MODEL_FAMILY')


def calibrate_apply(probabilities,calibration):
    p=check_probabilities(probabilities);t=float(calibration['temperature']);mix=float(calibration['prior_weight'])
    base=check_probabilities([calibration['prior']])[0]
    if not .25<=t<=16 or not 0<=mix<=1:raise EvidenceError('CALIBRATION_RANGE')
    return (1-mix)*softmax(np.log(np.maximum(p,1e-15))/t)+mix*base


def fit_calibration(p,y,base):
    p=check_probabilities(p);y=checked_labels(y);choices=[]
    for temp in (0.5,0.75,1.,1.5,2.,3.,4.,8.):
        for weight in (0.,.25,.5,.75,1.):
            c={'temperature':temp,'prior_weight':weight,'prior':list(base)}
            candidate=calibrate_apply(p,c);score=logloss(candidate,y)
            choices.append((score,temp,weight))
    score,temp,weight=min(choices,key=lambda x:(x[0],abs(math.log(x[1])),x[2]))
    return {'temperature':temp,'prior_weight':weight,'prior':list(base),'calibration_logloss':score,
            'calibration_rows':len(y),'choices':40,'objective':'CALIBRATION_ONLY_LOGLOSS'}


def fit_value(rows,penalty):
    x=features(rows,'compact');mean,std=normalize_fit(x);a=design(x,mean,std)
    # Targets are simulated executable-side price returns, never broker rewards.
    target=np.asarray([[r['long_after_spread_bps']-.9,r['short_after_spread_bps']-.9] for r in rows])
    if not np.isfinite(target).all() or not math.isfinite(penalty) or penalty<=0:raise EvidenceError('VALUE_TARGET_OR_PENALTY')
    reg=np.eye(a.shape[1])*penalty;reg[-1,-1]=0
    w=np.linalg.solve(a.T@a/len(a)+reg,a.T@target/len(a))
    return {'family':'ridge_executable_net','mean':mean.tolist(),'std':std.tolist(),'weights':w.tolist(),
            'penalty':penalty,'fit_rows':len(rows),'commission_bps':.4,'slippage_bps':.5,
            'actual_execution_rewards_used':0,'modeled_price_source':'HISTORICAL_NEXT_BAR_OPEN_NOT_GUARANTEED_FILL'}


def predict_value(model,rows):
    if model.get('family')!='ridge_executable_net':raise EvidenceError('VALUE_MODEL_FAMILY')
    return design(features(rows,'compact'),model['mean'],model['std'])@np.asarray(model['weights'])


def reliability(p,y):
    p=check_probabilities(p);y=checked_labels(y);reports=[]
    for k in range(3):
        bins=[];ece=0.
        for lo in np.arange(0,1,.1):
            mask=(p[:,k]>=lo)&(p[:,k]<(lo+.1) if lo<.9 else p[:,k]<=1)
            n=int(mask.sum())
            if n:
                predicted=float(p[mask,k].mean());actual=float((y[mask]==k).mean())
                ece+=n/len(y)*abs(predicted-actual);bins.append({'count':n,'predicted':predicted,'observed':actual})
        reports.append({'class':k,'ece_10bins':ece,'bins':bins})
    return {'multiclass_brier':float(brier(p,y).mean()),'logloss':logloss(p,y),
            'accuracy':float((p.argmax(1)==y).mean()),'reliability':reports}
