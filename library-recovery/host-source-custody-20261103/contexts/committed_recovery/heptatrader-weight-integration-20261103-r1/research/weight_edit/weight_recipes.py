"""Two bounded readout comparisons; all label-dependent edits are data fitting."""
import time


def augment_recipes(x,y,parent):
    import torch
    from torch.nn import functional as F
    x,y=x.double(),y.double();w,b=parent['value.weight'].double(),parent['value.bias'].double()
    output={};costs={};clock=time.perf_counter();cpu=time.process_time()
    head=torch.nn.Linear(768,1,dtype=torch.float32)
    with torch.no_grad():head.weight.zero_();head.bias.zero_()
    optimizer=torch.optim.SGD(head.parameters(),lr=.05);steps=0;cap=8192
    xf,yf=x.float(),y.float();last_loss=None
    while steps<cap and time.perf_counter()-clock<1.95:
        optimizer.zero_grad(set_to_none=True);loss=F.smooth_l1_loss(head(xf).flatten(),yf);loss.backward()
        torch.nn.utils.clip_grad_norm_(head.parameters(),1.,error_if_nonfinite=True);optimizer.step()
        with torch.no_grad():
            norm=torch.cat((head.weight.flatten(),head.bias)).norm()
            if norm>.1:head.weight.mul_(.1/norm);head.bias.mul_(.1/norm)
        steps+=1;last_loss=float(loss.detach())
    output['sgd_head_budget_2s']={'value.weight':head.weight.detach().clone(),'value.bias':head.bias.detach().clone()}
    costs['sgd_head_budget_2s']={'wall_seconds':time.perf_counter()-clock,'cpu_seconds':time.process_time()-cpu,'steps':steps,'step_cap':cap,'last_loss':last_loss,'stop':'step_cap' if steps==cap else 'wall_budget'}
    clock=time.perf_counter();cpu=time.process_time()
    mu=x.mean(0);xc=x-mu;pred=F.linear(x,w,b).flatten();residual=y-pred
    covariance=xc.T@(residual-residual.mean());u=F.normalize(covariance,dim=0)
    a=xc@u;den=float(a.square().sum());alpha=float(a@(residual-residual.mean()))/den if den>1e-20 else 0.
    theta=torch.cat((w.flatten(),b+residual.mean()));direction=torch.cat((u,-(mu@u).reshape(1)))
    aa=float(direction@direction);bb=2*float(theta@direction);cc=float(theta@theta)-.1**2
    allowed=(-bb+max(0.,bb*bb-4*aa*cc)**.5)/(2*aa) if aa>0 else 0.
    alpha=max(0.,min(alpha,allowed));patched=theta+alpha*direction
    output['covariance_direction_patch']={'value.weight':patched[:768].reshape(1,768).float(),'value.bias':patched[768:].float()}
    costs['covariance_direction_patch']={'wall_seconds':time.perf_counter()-clock,'cpu_seconds':time.process_time()-cpu,'alpha':alpha,'label_dependent_closed_form_fit':True}
    for name,cost in costs.items():
        cost.update(wall_budget_seconds=2.,shared_feature_cost_excluded=True)
        if cost['wall_seconds']>2.05:raise RuntimeError('CONSTRUCTION_BUDGET_EXCEEDED:'+name)
    return output,{'additional_compute':costs,'budget_protocol':'EACH_CONSTRUCTION_AT_MOST_2_SECONDS; TRAINING_8192_STEP_CAP; SHARED_FEATURE_COST_SEPARATE'}
