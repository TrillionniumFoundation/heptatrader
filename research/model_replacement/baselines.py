"""CPU fits and persisted readouts, with no broker dependencies."""
import time
import numpy as np
from .data import need
from .metrics import probabilities, targets, softmax, score
from research.neuron_learning.laya_cell.integrity import digest

def arrays(x, y=None):
    x = np.asarray(x, dtype=np.float64)
    need(x.ndim == 2 and len(x) and x.shape[1] and np.isfinite(x).all(), 'NUMERIC_FEATURE_MATRIX')
    return (x, targets(y, len(x))) if y is not None else x

class Prior:
    def fit(self, x, y, x_val, y_val):
        x, y = arrays(x,y); counts = np.bincount(y,minlength=7)
        self.p = (counts+1.)/(len(y)+7.)
        self.receipt = {'kind':'LAPLACE_1_PRIOR','training_rows':len(y),
                        'class_counts':counts.tolist(),'parameters':self.p.tolist(),'test_used':False}
        return self
    def predict_proba(self, x):
        return np.tile(self.p, (len(arrays(x)),1))
    def save(self, directory):
        return self.receipt

class LinearSoftmax:
    def __init__(self, steps=400, learning_rate=.05, l2=.01):
        need(type(steps) is int and 1 <= steps <= 10000 and 0 < learning_rate <= 1 and 0 <= l2 <= 10,
             'LINEAR_BUDGET')
        self.steps, self.learning_rate, self.l2 = steps, learning_rate, l2
    def transform(self, x):
        return np.clip((x-self.mean)/self.scale,-6,6)
    def fit(self, x, y, x_val, y_val):
        start=time.perf_counter(); x,y=arrays(x,y); xv,yv=arrays(x_val,y_val)
        need(x.shape[1]==xv.shape[1], 'FEATURE_DIMENSION_CHANGED')
        self.mean=x.mean(0); std=x.std(0); self.scale=np.where(std>=1e-6,std,1.)
        z,zv=self.transform(x),self.transform(xv)
        counts=np.bincount(y,minlength=7); w=np.zeros((x.shape[1],7)); b=np.log((counts+1.)/(len(y)+7.))
        initial=digest([w.tolist(),b.tolist()]); truth=np.eye(7)[y]; best=float('inf'); curve=[]
        for step in range(1,self.steps+1):
            residual=(softmax(z@w+b)-truth)/len(y)
            w-=self.learning_rate*(z.T@residual+self.l2*w)
            b-=self.learning_rate*residual.sum(0)
            if step==1 or step%20==0 or step==self.steps:
                loss=score(softmax(zv@w+b),yv)['mean_clipped_nll']
                curve.append({'step':step,'validation_nll':loss})
                if loss<best:
                    best=loss; self.weights=w.copy(); self.bias=b.copy(); self.best_step=step
        final=digest([self.weights.tolist(),self.bias.tolist()])
        self.receipt={'kind':'LINEAR_SOFTMAX','training_rows':len(y),'validation_rows':len(yv),
            'class_counts':counts.tolist(),'optimizer_steps':self.steps,'selected_step':self.best_step,
            'learning_rate':self.learning_rate,'l2_weights_only':self.l2,
            'standardization':'TRAIN_ONLY_STD_CLIP_6','trainable_parameters':int(w.size+b.size),
            'initial_parameter_sha256':initial,'final_parameter_sha256':final,'parameters_changed':initial!=final,
            'validation_curve':curve,'fit_seconds':time.perf_counter()-start,'test_used':False}
        return self
    def predict_proba(self,x):
        x=arrays(x); need(x.shape[1]==len(self.mean),'FEATURE_DIMENSION_CHANGED')
        return probabilities(softmax(self.transform(x)@self.weights+self.bias))
    def save(self,directory):
        return {**self.receipt,'mean':self.mean.tolist(),'scale':self.scale.tolist(),
                'weights':self.weights.tolist(),'bias':self.bias.tolist()}

class CatBoostCPU:
    def __init__(self, iterations=200, depth=4, threads=2, seed=20261102):
        need(type(iterations) is int and 1 <= iterations <= 2000 and 1 <= depth <= 8
             and type(threads) is int and 1 <= threads <= 16, 'CATBOOST_BUDGET')
        self.iterations,self.depth,self.threads,self.seed=iterations,depth,threads,seed
    def fit(self,x,y,x_val,y_val):
        try:
            import catboost
        except ImportError as exc:
            raise RuntimeError('CATBOOST_NOT_INSTALLED_IN_RESEARCH_ENV') from exc
        x,y=arrays(x,y); xv,yv=arrays(x_val,y_val)
        need(len(np.unique(y))>=2,'CATBOOST_REQUIRES_TWO_TRAIN_CLASSES')
        start=time.perf_counter()
        self.model=catboost.CatBoostClassifier(loss_function='MultiClass',eval_metric='MultiClass',classes_count=7,
            iterations=self.iterations,depth=self.depth,learning_rate=.05,l2_leaf_reg=5.,
            task_type='CPU',thread_count=self.threads,random_seed=self.seed,has_time=True,
            allow_writing_files=False,verbose=False,use_best_model=True)
        self.model.fit(x,y,eval_set=(xv,yv),early_stopping_rounds=30)
        self.receipt={'kind':'CATBOOST_CPU','version':catboost.__version__,'training_rows':len(y),
            'validation_rows':len(yv),'class_counts':np.bincount(y,minlength=7).tolist(),
            'trees':int(self.model.tree_count_),'parameters':self.model.get_params(),
            'fit_seconds':time.perf_counter()-start,'parameter_updates':'TRAINED_BOOSTED_TREES','test_used':False}
        return self
    def predict_proba(self,x):
        p=np.asarray(self.model.predict_proba(arrays(x)),dtype=np.float64)
        if p.shape[1]!=7:
            out=np.zeros((len(p),7)); classes=np.asarray(self.model.classes_,dtype=int)
            need(len(classes)==p.shape[1] and (classes>=0).all() and (classes<7).all(),'CATBOOST_CLASS_MAPPING')
            out[:,classes]=p; p=out
        return probabilities(p)
    def save(self,directory):
        from research.neuron_learning.laya_cell.integrity import sha_file
        path=directory/'catboost.cbm'; need(not path.exists(),'NEW_MODEL_OUTPUT_REQUIRED')
        self.model.save_model(str(path))
        return {**self.receipt,'model_file':{'path':str(path),'sha256':sha_file(path)}}

def restore(state):
    """Restore only JSON numeric state or a hash-pinned native CatBoost model."""
    kind=state.get('kind')
    if kind=='LAPLACE_1_PRIOR':
        model=Prior(); model.p=probabilities([state['parameters']])[0]
    elif kind=='LINEAR_SOFTMAX':
        model=LinearSoftmax()
        for name in ('mean','scale','weights','bias'):
            setattr(model,name,np.asarray(state[name],dtype=np.float64))
        need(model.weights.shape==(len(model.mean),7) and model.bias.shape==(7,)
             and model.scale.shape==model.mean.shape and (model.scale>0).all()
             and all(np.isfinite(getattr(model,n)).all() for n in ('mean','scale','weights','bias')),
             'LINEAR_STATE_SHAPE')
        need(digest([model.weights.tolist(),model.bias.tolist()])==state['final_parameter_sha256'],
             'LINEAR_PARAMETER_HASH')
    elif kind=='CATBOOST_CPU':
        from pathlib import Path
        from research.neuron_learning.laya_cell.integrity import sha_file
        import catboost
        pin=state['model_file']; need(sha_file(Path(pin['path']))==pin['sha256'],'CATBOOST_FILE_CHANGED')
        model=CatBoostCPU(); model.model=catboost.CatBoostClassifier(); model.model.load_model(pin['path'])
    else:
        raise ValueError('UNSUPPORTED_SAVED_MODEL')
    model.receipt=state
    return model
