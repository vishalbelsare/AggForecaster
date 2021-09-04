import torch
import torch.nn as nn
import torch.nn.functional as F

import numpy as np
import cvxpy as cp
import pywt
from scipy.linalg import block_diag

from utils import normalize, unnormalize, sqz, expand

device = 'cuda'

class DILATE(torch.nn.Module):
    """docstring for DILATE"""
    def __init__(self, base_models_dict, device):
        super(DILATE, self).__init__()
        self.base_models_dict = base_models_dict
        self.device = device

    def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
        return self.base_models_dict[1].to(self.device)(feats_in_dict[1], inputs_dict[1], feats_tgt_dict[1])

class MSE(torch.nn.Module):
    """docstring for MSE"""
    def __init__(self, base_models_dict, device):
        super(MSE, self).__init__()
        self.base_models_dict = base_models_dict
        self.device = device

    def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
        return self.base_models_dict[1](feats_in_dict[1].to(self.device), inputs_dict[1].to(self.device), feats_tgt_dict[1].to(self.device))

class NLL(torch.nn.Module):
    """docstring for NLL"""
    def __init__(self, base_models_dict, device):
        super(NLL, self).__init__()
        self.base_models_dict = base_models_dict
        self.device = device

    def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
        return self.base_models_dict[1](feats_in_dict[1].to(self.device), inputs_dict[1].to(self.device), feats_tgt_dict[1].to(self.device))

class CNNRNN(torch.nn.Module):
    """docstring for NLL"""
    def __init__(self, base_models_dict, device):
        super(CNNRNN, self).__init__()
        self.base_models_dict = base_models_dict
        self.device = device

    def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
        return self.base_models_dict[1](
            feats_in_dict[1].to(self.device),
            inputs_dict[1].to(self.device),
            feats_tgt_dict[1].to(self.device)
        )

class RNNNLLNAR(torch.nn.Module):
    """docstring for NLL"""
    def __init__(self, base_models_dict, device, is_oracle=False):
        super(RNNNLLNAR, self).__init__()
        self.base_models_dict = base_models_dict
        self.device = device
        self.is_oracle = is_oracle

    def forward(self, dataset, norms, which_split):
        feats_in = dataset['sum'][1][2].to(self.device)
        inputs = dataset['sum'][1][0].to(self.device)
        feats_tgt = dataset['sum'][1][3].to(self.device)
        target = dataset['sum'][1][1].to(self.device)
        #if self.is_oracle:
        #    target = dataset['sum'][1][1].to(self.device)
        #else:
        #    target = None
        ids = dataset['sum'][1][4].cpu()

        mdl = self.base_models_dict['sum'][1]
        with torch.no_grad():
            out = mdl(feats_in, inputs, feats_tgt, target)
            if mdl.is_signature:
                if mdl.estimate_type in ['point']:
                    pred_mu, _, _ = out
                elif mdl.estimate_type in ['variance']:
                    pred_mu, pred_d, _, _ = out
                elif mdl.estimate_type in ['covariance']:
                    pred_mu, pred_d, pred_v, _, _ = out
            else:
                if mdl.estimate_type in ['point']:
                    pred_mu = out
                elif mdl.estimate_type in ['variance']:
                    pred_mu, pred_d = out
                elif mdl.estimate_type in ['covariance']:
                    pred_mu, pred_d, pred_v = out
        pred_mu = pred_mu.cpu()

        if mdl.estimate_type in ['covariance']:
            pred_d = pred_d.cpu()
            pred_v = pred_v.cpu()

            dist = torch.distributions.lowrank_multivariate_normal.LowRankMultivariateNormal(
                torch.squeeze(pred_mu, dim=-1), pred_v, torch.squeeze(pred_d, dim=-1)
            )
            pred_std = torch.diagonal(dist.covariance_matrix, dim1=-2, dim2=-1).unsqueeze(dim=-1)
            if which_split in ['test']:
                pred_std = norms['sum'][1].unnormalize(pred_std[..., 0], ids=ids, is_var=True).unsqueeze(-1)
        elif mdl.estimate_type in ['variance']:
            pred_d = pred_d.cpu()
            pred_std = pred_d
            pred_v = torch.ones_like(pred_mu) * 1e-9
            if which_split in ['test']:
                pred_std = norms['sum'][1].unnormalize(pred_std[..., 0], ids=ids, is_var=True).unsqueeze(-1)
        else:
            pred_d = torch.ones_like(pred_mu) * 1e-9
            pred_v = torch.ones_like(pred_mu) * 1e-9
            pred_std = torch.ones_like(pred_mu) * 1e-9

        if which_split in ['test']:
            pred_mu = norms['sum'][1].unnormalize(pred_mu[..., 0], ids=ids, is_var=False).unsqueeze(-1)

        return (pred_mu, pred_d, pred_v, pred_std)

class RNN_MSE_NAR(torch.nn.Module):
    
    def __init__(self, base_models_dict, device):
        super(RNN_MSE_NAR, self).__init__()
        self.base_models_dict = base_models_dict
        self.device = device

    def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
        return self.base_models_dict[1](
            feats_in_dict[1].to(self.device),
            inputs_dict[1].to(self.device),
            feats_tgt_dict[1].to(self.device)
        )


class DualTPP(torch.nn.Module):
    """docstring for DualTPP"""
    def __init__(self, K_list, base_models_dict, aggregates, device):
        '''
        K: int
            number of steps to aggregate at each level
        base_models_dict: dict
            key: level in the hierarchy
            value: base model at the level 'key'
        '''
        super(DualTPP, self).__init__()
        self.K_list = K_list
        self.base_models_dict = base_models_dict
        self.aggregates = aggregates
        self.device = device

    #def aggregate_seq_(self, seq, K):
    #    assert seq.shape[0]%K == 0
    #    agg_seq = np.array([[1./K * cp.sum(seq[i:i+K])] for i in range(0, seq.shape[0], K)])
    #    agg_seq = torch.tensor([[1./K * cp.sum(seq[i:i+K])] for i in range(0, seq.shape[0], K)])
    #    import ipdb ; ipdb.set_trace()
    #    return agg_seq
    def aggregate_data(self, y, agg, K):
        if agg == 'sum':
            return 1./y.shape[1] * cp.sum(y, axis=1, keepdims=True)
        elif agg == 'slope':
            if K==1:
                return y
            #x = torch.arange(y.shape[0], dtype=torch.float)
            x = torch.arange(y.shape[1], dtype=torch.float).unsqueeze(0).repeat(y.shape[0], 1)
            m_x = x.mean(dim=1, keepdims=True)
            s_xx = ((x-m_x)**2).sum(dim=1, keepdims=True)
            a = (x - m_x) / s_xx
            w = cp.sum(cp.multiply(a, y), axis=1, keepdims=True)
            return w

    #def log_prob(self, ex_preds, means, std):
    #    return -cp.sum(np.sum(np.log(1/(((2*np.pi)**0.5)*std)) - (((ex_preds - means)**2) / (2*(std)**2))))
    def log_prob(self, x_, means, std):
        return -cp.sum(cp.log(1/(((2*np.pi)**0.5)*std)) - (((x_ - means)**2) / (2.*(std)**2)))

    def optimize(self, params_dict, norm_dict):

        for lvl, params in params_dict.items():
            #params[0] = unnormalize(params[0].detach().numpy(), norm_dict[lvl].detach().numpy())
            if lvl==1:
                ex_preds = cp.Variable(params[0].shape)
                lvl_ex_preds = ex_preds
            else:
                lvl_ex_preds, _ = normalize(
                    self.aggregate_seq_(unnormalize(ex_preds, norm_dict[1], is_var=False), lvl),
                    norm_dict[lvl]
                )
            lvl_loss = self.log_prob(lvl_ex_preds, params[0].detach().numpy(), params[1].detach().numpy())
            #import ipdb
            #ipdb.set_trace()
            if lvl==1:
                opt_loss = lvl_loss
            else:
                opt_loss += lvl_loss


        objective = cp.Minimize(opt_loss)

        #constraints = [ex_preds>=0]

        prob = cp.Problem(objective)#, constraints)

        try:
            opt_loss = prob.solve()
        except cp.error.SolverError:
            opt_loss = prob.solve(solver='SCS')

        #if ex_preds.value is None:

        #import ipdb
        #ipdb.set_trace()

        return ex_preds.value


    #def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
    def forward(self, dataset, norms):

        #inputs_dict = dataset['sum'][1]
        #import ipdb ; ipdb.set_trace()

        #norm_dict_np = dict()
        #for lvl in norm_dict.keys():
        #    norm_dict_np[lvl] = norm_dict[lvl].detach().numpy()

        params_dict = {}
        for agg in self.aggregates:
            params_dict[agg] = {}
            for level in self.K_list:
                model = self.base_models_dict[agg][level]
                inputs = dataset[agg][level][0]
                feats_in, feats_tgt = dataset[agg][level][2], dataset[agg][level][3]
                ids = dataset[agg][level][4].cpu()

                with torch.no_grad():
                    if model.estimate_type in ['point']:
                        means = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )
                    elif model.estimate_type in ['variance']:
                        means, d = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )
                    elif model.estimate_type in ['covariance']:
                        means, d, v = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )
                means = means.cpu()

                if model.estimate_type is 'covariance':
                    d = d.cpu()
                    v = v.cpu()

                    dist = torch.distributions.lowrank_multivariate_normal.LowRankMultivariateNormal(
                        means.squeeze(dim=-1), v, d.squeeze(dim=-1)
                    )
                    stds = torch.diagonal(dist.covariance_matrix, dim1=-2, dim2=-1).unsqueeze(dim=-1)
                elif model.estimate_type is 'variance':
                    stds = d.cpu()
                    v = torch.ones_like(means) * 1e-9
                    stds = norms[agg][level].unnormalize(stds[..., 0], ids=ids, is_var=True).unsqueeze(-1)
                else:
                    d = torch.ones_like(means) * 1e-9
                    v = torch.ones_like(means) * 1e-9
                    stds = torch.ones_like(means) * 1e-9

                means = norms[agg][level].unnormalize(means[..., 0], ids=ids, is_var=False).unsqueeze(-1)

                params = [means, stds, d, v]
                params_dict[agg][level] = params

        #import ipdb ; ipdb.set_trace()

        base_lvl = self.aggregates[0]
        bs, N = params_dict[base_lvl][1][0].shape[0], params_dict[base_lvl][1][0].shape[1]
        x = cp.Variable((bs, N))
        x_dict = {}
        opt_loss = 0.

        for agg in self.aggregates:
            x_dict[agg] = {}
            for lvl in self.K_list:
                lvl_x = []
                for i in range(0, N, lvl):
                    lvl_x.append(self.aggregate_data(x[:, i:i+lvl], agg, lvl))
                x_dict[agg][lvl] = lvl_x

            for lvl, params in params_dict[agg].items():
                for idx, i in enumerate(range(0, N, lvl)):
                    loss = self.log_prob(
                            x_dict[agg][lvl][idx],
                            params[0][..., idx:idx+1, 0].detach(),
                            params[1][..., idx:idx+1, 0].detach()
                    )
                    opt_loss += loss

        objective = cp.Minimize(opt_loss)

        prob = cp.Problem(objective)

        try:
            opt_loss = prob.solve()
        except cp.error.SolverError:
            opt_loss = prob.solve(solver='SCS')

        all_preds_mu = torch.FloatTensor(x.value).unsqueeze(dim=-1)
        all_preds_std = params_dict[base_lvl][1][1]
        d = params_dict[base_lvl][1][2]
        v = params_dict[base_lvl][1][3]

        return all_preds_mu, d, v, all_preds_std


class DualTPP_CF(torch.nn.Module):
    """docstring for DualTPP"""
    def __init__(self, K_list, base_models_dict, aggregates, device, opt_normspace=False):
        '''
        K: int
            number of steps to aggregate at each level
        base_models_dict: dict
            key: level in the hierarchy
            value: base model at the level 'key'
        '''
        super(DualTPP_CF, self).__init__()
        self.K_list = K_list # [1, 6, 12]
        self.base_models_dict = base_models_dict
        self.aggregates = aggregates # ['sum', 'slope']
        self.device = device
        self.opt_normspace = opt_normspace

    def get_A(self, agg, K, bs, N):
        #A_ = torch.block_diag(*[torch.ones(K)*1./K]*(N//K)).unsqueeze(dim=0).repeat(bs, 1, 1)
        if agg == 'sum':
            a = torch.ones(K)*1./K
        elif agg == 'slope':
            if K==1:
                a = torch.ones(K)
            else:
                x = torch.arange(K, dtype=torch.float)
                m_x = x.mean()
                s_xx = ((x-m_x)**2).sum()
                a = (x - m_x) / s_xx

        #import ipdb ; ipdb.set_trace()

        A_ = torch.block_diag(*[torch.ones(K)*a]*(N//K)).unsqueeze(dim=0).repeat(bs, 1, 1)
        import ipdb ; ipdb.set_trace()
        #sig_ = torch.block_diag(*[torch.ones(K)*a]*(N//K)).unsqueeze(dim=0).repeat(bs, 1, 1)
        #import ipdb ; ipdb.set_trace()

        return A_


    def revise(self, params_dict, norm_dict):
        N = len(params_dict[1][0])
        K = self.K_list[1]

        coeffs_bottom = np.eye(N)
        coeffs_agg = block_diag(*[np.ones(K)]*(N//K))
        coeffs = np.concatenate([coeffs_bottom, coeffs_agg], axis=0)

        y = np.concatenate(
            [
                unnormalize(params_dict[1][0].detach().numpy(), norm_dict[1], is_var=False),
                unnormalize(params_dict[K][0].detach().numpy(), norm_dict[K], is_var=False),
            ],
            axis=0
                )

        #import ipdb
        #ipdb.set_trace()

        A = coeffs
        y_revised = np.matmul(np.linalg.pinv(np.matmul(A.T, A)), np.matmul(A.T, y))

        return normalize(y_revised[:N], norm_dict[1], is_var=False)[0]

    #def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
    def forward(self, dataset, norms, which_split):

        params_dict = {}
        ids_dict = {}
        for agg in self.aggregates:
            params_dict[agg] = {}
            ids_dict[agg] = {}
            for level in self.K_list:
                model = self.base_models_dict[agg][level]
                inputs = dataset[agg][level][0]
                feats_in, feats_tgt = dataset[agg][level][2], dataset[agg][level][3]
                ids = dataset[agg][level][4].cpu()

                with torch.no_grad():
                    if model.estimate_type in ['point']:
                        means = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )
                    elif model.estimate_type in ['variance']:
                        means, d = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )
                    elif model.estimate_type in ['covariance']:
                        means, d, v = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )

                means = means.cpu()
                if model.estimate_type is 'covariance':
                    d = d.cpu()
                    v = v.cpu()

                    dist = torch.distributions.lowrank_multivariate_normal.LowRankMultivariateNormal(
                        means.squeeze(dim=-1), v, d.squeeze(dim=-1)
                    )
                    stds = torch.diagonal(dist.covariance_matrix, dim1=-2, dim2=-1).unsqueeze(dim=-1)
                elif model.estimate_type is 'variance':
                    stds = d.cpu()
                    v = torch.ones_like(means) * 1e-9
                    if not self.opt_normspace:
                        stds = norms[agg][level].unnormalize(stds[..., 0], ids=ids, is_var=True).unsqueeze(-1)
                else:
                    d = torch.ones_like(means) * 1e-9
                    v = torch.ones_like(means) * 1e-9
                    stds = torch.ones_like(means) * 1e-9

                if not self.opt_normspace:
                    means = norms[agg][level].unnormalize(means[..., 0], ids=ids, is_var=False).unsqueeze(-1)

                params = [means, stds, d, v]
                params_dict[agg][level] = params
                ids_dict[agg][level] = ids


        base_lvl = self.aggregates[0]
        bs, N = params_dict[base_lvl][1][0].shape[0], params_dict[base_lvl][1][0].shape[1]
        A = []
        for agg in self.aggregates:
            for K in self.K_list:
                #A_ = torch.block_diag(*[torch.ones(K)*1./K]*(N//K)).unsqueeze(dim=0).repeat(bs, 1, 1)
                A_ = self.get_A(agg, K, bs, N)
                A.append(A_)
                #import ipdb ; ipdb.set_trace()
        A = torch.cat(A, dim=1)

        b = []
        for agg in self.aggregates:
            for K in self.K_list:
                b_ = params_dict[agg][K][0]
                b.append(b_)
        b = torch.cat(b, dim=1)

        x = torch.matmul(torch.inverse(torch.matmul(A.transpose(1, 2), A)), torch.matmul(A.transpose(1, 2), b))

        all_preds_mu = x
        all_preds_std = params_dict[base_lvl][1][1]
        d = params_dict[base_lvl][1][2]
        v = params_dict[base_lvl][1][3]

        return all_preds_mu, d, v, all_preds_std


class KLInference(torch.nn.Module):
    """docstring for DualTPP"""
    def __init__(self, K_list, base_models_dict, aggregates, device, opt_normspace=False):
        '''
        K: int
            number of steps to aggregate at each level
        base_models_dict: dict
            key: level in the hierarchy
            value: base model at the level 'key'
        '''
        super(KLInference, self).__init__()
        self.K_list = K_list
        self.base_models_dict = base_models_dict
        self.aggregates = aggregates
        self.device = device
        self.opt_normspace = opt_normspace

    def aggregate_data(self, y, agg, K, is_var):
        if agg == 'sum' and not is_var:
            return 1./y.shape[1] * cp.sum(y, axis=1, keepdims=True)
        elif agg == 'sum' and is_var:
            return 1./y.shape[1]**2 * cp.sum(y, axis=1, keepdims=True)
        elif agg == 'slope':
            if K==1:
                return y
            #x = torch.arange(y.shape[0], dtype=torch.float)
            x = torch.arange(y.shape[1], dtype=torch.float).unsqueeze(0).repeat(y.shape[0], 1)
            m_x = x.mean(dim=1, keepdims=True)
            s_xx = ((x-m_x)**2).sum(dim=1, keepdims=True)
            a = (x - m_x) / s_xx
            if not is_var:
                w = cp.sum(cp.multiply(a, y), axis=1, keepdims=True)
                return w
            else:
                w = cp.sum(cp.multiply(a**2, y), axis=1, keepdims=True)
                return w

    def KL_loss(self, x_mu, x_var, mu, std):
        return cp.sum(cp.log(std) - cp.log(x_var)/2. + (x_var + (mu-x_mu)**2)/(2*std**2) - 0.5)
        #return cp.sum(cp.log(std) + (x_var + (mu-x_mu)**2)/(2*std**2) - 0.5)

    def log_prob(self, x_, means, std):
        return -cp.sum(cp.log(1/(((2*np.pi)**0.5)*std)) - (((x_ - means)**2) / (2.*(std)**2)))

    #def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
    def forward(self, dataset, norms, which_split):

        #inputs_dict = dataset['sum'][1]
        #import ipdb ; ipdb.set_trace()

        #norm_dict_np = dict()
        #for lvl in norm_dict.keys():
        #    norm_dict_np[lvl] = norm_dict[lvl].detach().numpy()

        params_dict = {}
        ids_dict = {}
        for agg in self.aggregates:
            params_dict[agg] = {}
            ids_dict[agg] = {}
            for level in self.K_list:
                model = self.base_models_dict[agg][level]
                inputs = dataset[agg][level][0]
                feats_in, feats_tgt = dataset[agg][level][2], dataset[agg][level][3]
                ids = dataset[agg][level][4].cpu()

                with torch.no_grad():
                    if model.estimate_type in ['point']:
                        means, d, v = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )
                    elif model.estimate_type in ['variance']:
                        means, d = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )
                    elif model.estimate_type in ['covariance']:
                        means, d, v = model(
                            feats_in.to(self.device), inputs.to(self.device),
                            feats_tgt.to(self.device)
                        )

                means = means.cpu()
                if model.estimate_type is 'covariance':
                    d = d.cpu()
                    v = v.cpu()

                    dist = torch.distributions.lowrank_multivariate_normal.LowRankMultivariateNormal(
                        means.squeeze(dim=-1), v, d.squeeze(dim=-1)
                    )
                    stds = torch.diagonal(dist.covariance_matrix, dim1=-2, dim2=-1).unsqueeze(dim=-1)
                elif model.estimate_type is 'variance':
                    stds = d.cpu()
                    v = torch.ones_like(means) * 1e-9
                    if not self.opt_normspace:
                        stds = norms[agg][level].unnormalize(stds[..., 0], ids=ids, is_var=True).unsqueeze(-1)
                else:
                    d = torch.ones_like(means) * 1e-9
                    v = torch.ones_like(means) * 1e-9
                    stds = torch.ones_like(means) * 1e-9

                if not self.opt_normspace:
                    means = norms[agg][level].unnormalize(means[..., 0], ids=ids, is_var=False).unsqueeze(-1)

                params = [means, stds, d, v]
                params_dict[agg][level] = params
                ids_dict[agg][level] = ids

        #import ipdb ; ipdb.set_trace()

        base_lvl = self.aggregates[0]
        bs, N = params_dict[base_lvl][1][0].shape[0], params_dict[base_lvl][1][0].shape[1]
        x_mu = cp.Variable((bs, N))
        x_var = cp.Variable((bs, N))
        x_mu_dict, x_var_dict = {}, {}
        opt_loss = 0.
        all_preds_mu, all_preds_std = [], []

        opt_bs = bs
        for bch in range(0, bs, opt_bs):
            #print('Example:', bch)
            try:
                for agg in self.aggregates:
                    x_mu_dict[agg], x_var_dict[agg] = {}, {}
                    for lvl in self.K_list:

                        base_lvl_present = False
                        if lvl==1: # If lvl=1 present in other aggregates, ignore it
                            #import ipdb ; ipdb.set_trace()
                            other_aggs = set(self.aggregates) - {agg}
                            for other_agg in other_aggs:
                                if x_mu_dict.get(other_agg, -1) is not -1:
                                    base_lvl_present = True

                        if not base_lvl_present:
                            #x_mu_dict[agg][lvl], x_var_dict[agg][lvl] = {}, {}
                            lvl_x_mu, lvl_x_var = [], []
                            for i in range(0, N, lvl):
                                lvl_x_mu.append(self.aggregate_data(x_mu[bch:bch+opt_bs, i:i+lvl], agg, lvl, is_var=False))
                                lvl_x_var.append(self.aggregate_data(x_var[bch:bch+opt_bs, i:i+lvl], agg, lvl, is_var=True))
                            x_mu_dict[agg][lvl] = lvl_x_mu
                            x_var_dict[agg][lvl] = lvl_x_var

                        #for lvl, params in params_dict[agg].items():
                            params = params_dict[agg][lvl]
                            for idx, _ in enumerate(range(0, N, lvl)):
                                #import ipdb ; ipdb.set_trace()
                                loss = self.KL_loss(
                                    x_mu_dict[agg][lvl][idx],
                                    x_var_dict[agg][lvl][idx],
                                    params[0][bch:bch+opt_bs, idx:idx+1, 0].detach(),
                                    params[1][bch:bch+opt_bs, idx:idx+1, 0].detach()
                                )
                                opt_loss += loss
                #import ipdb ; ipdb.set_trace()

                objective = cp.Minimize(opt_loss)

                constraints = [x_var>=1e-6]

                prob = cp.Problem(objective, constraints)

                #x_mu.value = params_dict[base_lvl][1][0]
                #x_var.value = params_dict[base_lvl][1][1]

                opt_loss = prob.solve()
            except cp.error.SolverError:
                for agg in self.aggregates:
                    x_mu_dict[agg], x_var_dict[agg] = {}, {}
                    for lvl in self.K_list:

                        base_lvl_present = False
                        if lvl==1: # If lvl=1 present in other aggregates, ignore it
                            #import ipdb ; ipdb.set_trace()
                            other_aggs = set(self.aggregates) - {agg}
                            for other_agg in other_aggs:
                                if x_mu_dict.get(other_agg, -1) is not -1:
                                    base_lvl_present = True

                        if not base_lvl_present:
                            #x_mu_dict[agg][lvl], x_var_dict[agg][lvl] = {}, {}
                            lvl_x_mu, lvl_x_var = [], []
                            for i in range(0, N, lvl):
                                lvl_x_mu.append(self.aggregate_data(x_mu[bch:bch+opt_bs, i:i+lvl], agg, lvl, is_var=False))
                                lvl_x_var.append(self.aggregate_data(x_var[bch:bch+opt_bs, i:i+lvl], agg, lvl, is_var=True))
                            x_mu_dict[agg][lvl] = lvl_x_mu
                            x_var_dict[agg][lvl] = lvl_x_var

                        #for lvl, params in params_dict[agg].items():
                            params = params_dict[agg][lvl]
                            for idx, _ in enumerate(range(0, N, lvl)):
                                #import ipdb ; ipdb.set_trace()
                                loss = self.log_prob(
                                    x_mu_dict[agg][lvl][idx],
                                    params[0][bch:bch+opt_bs, idx:idx+1, 0].detach(),
                                    params[1][bch:bch+opt_bs, idx:idx+1, 0].detach()
                                )
                                opt_loss += loss
                #import ipdb ; ipdb.set_trace()

                objective = cp.Minimize(opt_loss)

                #constraints = [x_var>=1e-9]

                prob = cp.Problem(objective)#, constraints)

                #opt_loss = prob.solve(solver='SCS')
                opt_loss = prob.solve()

                x_var.value = params_dict[base_lvl][1][1].detach().numpy()[..., 0]**2

            #import ipdb ; ipdb.set_trace()

            all_preds_mu.append(torch.FloatTensor(x_mu.value).unsqueeze(dim=-1))
            all_preds_std.append(torch.sqrt(torch.FloatTensor(x_var.value).unsqueeze(dim=-1)))

        #all_preds_mu = torch.FloatTensor(x_mu.value).unsqueeze(dim=-1)
        #all_preds_std = torch.sqrt(torch.FloatTensor(x_var.value).unsqueeze(dim=-1))
        all_preds_mu = torch.cat(all_preds_mu, dim=0)
        all_preds_std = torch.cat(all_preds_std, dim=0)
        if which_split in ['test'] and self.opt_normspace:
            all_preds_mu = norms[base_lvl][1].unnormalize(
                all_preds_mu[..., 0], ids=ids_dict[base_lvl][1], is_var=False
            ).unsqueeze(-1)
            all_preds_std = norms[base_lvl][1].unnormalize(
                all_preds_std[..., 0], ids=ids_dict[base_lvl][1], is_var=True
            ).unsqueeze(-1)
        if which_split in ['dev'] and not self.opt_normspace:
            all_preds_mu = norms[base_lvl][1].normalize(
                all_preds_mu[..., 0], ids=ids_dict[base_lvl][1], is_var=False
            )
            all_preds_std = norms[base_lvl][1].normalize(
                all_preds_std[..., 0], ids=ids_dict[base_lvl][1], is_var=True
            )

        d = params_dict[base_lvl][1][2]
        v = params_dict[base_lvl][1][3]

        return all_preds_mu, d, v, all_preds_std



class OPT_st(torch.nn.Module):
    """docstring for OPT_st"""
    def __init__(self, K_list, base_models_dict, device, disable_sum=False):
        '''
        K_list: list
            list of K-values used for aggregation
        base_models_dict: dict of dicts
            key: aggregation method
            value: dict
                key: level in the hierarchy
                value: base model at the level 'key'
        '''
        super(OPT_st, self).__init__()
        self.K_list = K_list
        self.base_models_dict = base_models_dict
        self.disable_sum = disable_sum
        self.device = device


    def aggregate_seq_(self, seq, K):
        assert seq.shape[0]%K == 0
        agg_seq = np.array([[1./K * cp.sum(seq[i:i+K])] for i in range(0, seq.shape[0], K)])
        return agg_seq

    def fit_slope_with_indices(self, seq, K):
        W = []
        x = np.cumsum(np.ones((tuple([K]) + seq.shape[1:])), axis=0) - 1.
        m_x = np.mean(x, axis=0)
        s_xx = np.sum((x-m_x)**2, axis=0)
        a = (x - m_x) / s_xx
        for i in range(0, seq.shape[0], K):
            y = seq[i:i+K]
            w = cp.sum(cp.multiply(a, y), axis=0, keepdims=True)
            W.append(w)

        #W = np.expand_dims(np.array(W), axis=1)
        #W = np.array(W)
        W = cp.vstack(W)
        return W

    def log_prob(self, ex_preds, means, std):
        #import ipdb
        #ipdb.set_trace()
        return -cp.sum(np.sum(np.log(1/(((2*np.pi)**0.5)*std)) - (((ex_preds - means)**2) / (2*(std)**2))))

    def optimize(self, params_dict, norm_dict):

        ex_preds = cp.Variable(params_dict['sum'][1][0].shape)
        for lvl, params in params_dict['slope'].items():
            if lvl==1:
                lvl_ex_preds = ex_preds
            else:
                lvl_ex_preds, _ = normalize(
                    self.fit_slope_with_indices(
                        unnormalize(ex_preds, norm_dict['slope'][1], is_var=False),
                        lvl
                    ),
                    norm_dict['slope'][lvl]
                )
            lvl_loss = self.log_prob(
                lvl_ex_preds,
                params_dict['slope'][lvl][0].detach().numpy(),
                params_dict['slope'][lvl][1].detach().numpy()
            )
            if lvl==1:
                opt_loss = lvl_loss
            else:
                opt_loss += lvl_loss

        if not self.disable_sum:
            for lvl, params in params_dict['sum'].items():
                if lvl==1:
                    lvl_ex_preds = ex_preds
                else:
                    lvl_ex_preds, _ = normalize(
                        self.aggregate_seq_(
                            unnormalize(ex_preds, norm_dict['sum'][1], is_var=False),
                            lvl
                        ),
                        norm_dict['sum'][lvl]
                    )
                lvl_loss = self.log_prob(
                    lvl_ex_preds,
                    params_dict['sum'][lvl][0].detach().numpy(),
                    params_dict['sum'][lvl][1].detach().numpy()
                )
                opt_loss += lvl_loss

        objective = cp.Minimize(opt_loss)

        #constraints = [ex_preds>=0]

        prob = cp.Problem(objective)#, constraints)

        try:
            opt_loss = prob.solve()
        except cp.error.SolverError:
            opt_loss = prob.solve(solver='SCS')

        #if ex_preds.value is None:

        #import ipdb
        #ipdb.set_trace()

        return ex_preds.value


    def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
        '''
        inputs_dict: [aggregation method][level]
        norm_dict: [aggregation method][level]
        '''

        norm_dict_np = dict()
        for agg_method in norm_dict.keys():
            norm_dict_np[agg_method] = dict()
            for lvl in norm_dict[agg_method].keys():
                norm_dict_np[agg_method][lvl] = norm_dict[agg_method][lvl].detach().numpy()

        params_dict = dict()
        for agg_method in self.base_models_dict.keys():
            params_dict[agg_method] = dict()
            if agg_method in ['slope', 'sum']:
                for level in self.K_list:
                    print(agg_method, level)
                    model = self.base_models_dict[agg_method][level]
                    inputs = inputs_dict[agg_method][level]
                    feats_in, feats_tgt = feats_in_dict[agg_method][level], feats_tgt_dict[agg_method][level]
                    means, stds = model(feats_in.to(self.device), inputs.to(self.device), feats_tgt.to(self.device))
                    means = means.cpu()
                    if stds is not None:
                        stds = stds.cpu()

                    if targets_dict is not None and level != 1:
                        means = targets_dict[agg_method][level]

                    if model.point_estimates:
                        stds = torch.ones_like(means)
                    params = [means, stds]
                    params_dict[agg_method][level] = params

        all_preds_mu = []
        all_preds_std = []
        for i in range(params_dict['sum'][1][0].size()[0]):
            #print(i)
            ex_params_dict = dict()
            ex_norm_dict = dict()
            for agg_method in params_dict.keys():
                ex_params_dict[agg_method] = dict()
                ex_norm_dict[agg_method] = dict()
                for lvl in params_dict[agg_method].keys():
                    ex_params_dict[agg_method][lvl] = [params_dict[agg_method][lvl][0][i], params_dict[agg_method][lvl][1][i]]
                    ex_norm_dict[agg_method][lvl] = norm_dict_np[agg_method][lvl][i]

            #import ipdb
            #ipdb.set_trace()
            ex_preds_opt = self.optimize(ex_params_dict, ex_norm_dict)
            all_preds_mu.append(ex_preds_opt)
            all_preds_std.append(params_dict['sum'][1][1][i])

        all_preds_mu = torch.FloatTensor(all_preds_mu)
        all_preds_std = torch.stack(all_preds_std)

        #all_preds, _ = normalize(all_preds, norm_dict[0])

        return all_preds_mu, all_preds_std

class OPT_KL_st(OPT_st):
    """docstring for OPT_st"""
    def __init__(self, K_list, base_models_dict, agg_methods, device):
        '''
        K_list: list
            list of K-values used for aggregation
        base_models_dict: dict of dicts
            key: aggregation method
            value: dict
                key: level in the hierarchy
                value: base model at the level 'key'
        agg_methods: list
            list of aggregate methods to use
        '''
        super(OPT_KL_st, self).__init__(K_list, base_models_dict, device)
        self.agg_methods = agg_methods
        self.device = device


    def aggregate_seq_(self, mu, var, K):
        assert mu.shape[0]%K == 0
        agg_mu = np.array([[1./K * cp.sum(mu[i:i+K])] for i in range(0, mu.shape[0], K)])
        agg_var = np.array([[1./(K*K) * cp.sum(var[i:i+K])] for i in range(0, var.shape[0], K)])
        return agg_mu, agg_var

    def fit_slope_with_indices(self, mu, var, K):
        W_mu = []
        W_var = []
        x = np.cumsum(np.ones((tuple([K])+mu.shape[1:])), axis=0) - 1.
        m_x = np.mean(x, axis=0)
        s_xx = np.sum((x-m_x)**2, axis=0)
        a = (x - m_x) / s_xx
        for i in range(0, mu.shape[0], K):
            y_mu = mu[i:i+K]
            y_var = var[i:i+K]
            w_mu = cp.sum(cp.multiply(a, y_mu), axis=0, keepdims=True)
            w_var = cp.sum(cp.multiply(a**2, y_var), axis=0, keepdims=True)
            W_mu.append(w_mu)
            W_var.append(w_var)
        #W_mu = np.expand_dims(np.array(W_mu), axis=1)
        #W_var = np.expand_dims(np.array(W_var), axis=1)
        W_mu = cp.vstack(W_mu)
        W_var = cp.vstack(W_var)
        return W_mu, W_var

    def log_prob(self, ex_preds, means, std):
        #import ipdb
        #ipdb.set_trace()
        return -cp.sum(np.sum(np.log(1/(((2*np.pi)**0.5)*std)) - (((ex_preds - means)**2) / (2*(std)**2))))

    def KL(self, mu_1, var_1, mu_2, var_2, lvl):

        def single_eqn_kl(mu_1, var_1, mu_2, var_2):
            return cp.sum(cp.log(var_1)/2. - cp.log(var_2)/2. + (var_2 + (mu_2-mu_1)**2)/(2*var_1) - 0.5)

        kl_distance = 0.
        if lvl != 1:
            for i in range(mu_1.shape[0]):
                kl_distance += (single_eqn_kl(mu_1[i,0], var_1[i,0], mu_2[i,0], var_2[i,0]))
        else:
            kl_distance = single_eqn_kl(mu_1, var_1, mu_2, var_2)

        return kl_distance


    def optimize(self, params_dict, norm_dict):

        ex_mu = cp.Variable(params_dict[self.agg_methods[0]][1][0].shape)
        ex_var = cp.Variable(params_dict[self.agg_methods[0]][1][1].shape)
        for agg_id, agg_method in enumerate(self.agg_methods):
            for lvl, params in params_dict[agg_method].items():
                if lvl==1:
                    lvl_ex_mu = ex_mu
                    lvl_ex_var = ex_var
                else:
                    if agg_method in ['slope']:
                        lvl_ex_mu, lvl_ex_var = self.fit_slope_with_indices(
                            unnormalize(ex_mu, norm_dict[agg_method][1], is_var=False),
                            unnormalize(ex_var, norm_dict[agg_method][1]**2, is_var=True),
                            lvl
                        )
                    if agg_method in ['sum']:
                        lvl_ex_mu, lvl_ex_var = self.aggregate_seq_(
                            unnormalize(ex_mu, norm_dict[agg_method][1], is_var=False),
                            unnormalize(ex_var, norm_dict[agg_method][1]**2, is_var=True),
                            lvl
                        )
                    lvl_ex_mu, _ = normalize(lvl_ex_mu, norm_dict[agg_method][lvl])
                    lvl_ex_var, _ = normalize(lvl_ex_var, norm_dict[agg_method][lvl]**2, is_var=True)
                lvl_loss = self.KL(
                    params_dict[agg_method][lvl][0].detach().numpy(),
                    params_dict[agg_method][lvl][1].detach().numpy()**2,
                    lvl_ex_mu, lvl_ex_var, lvl
                )
                if agg_id==0 and lvl==1:
                    opt_loss = lvl_loss
                else:
                    opt_loss += lvl_loss

        #for lvl, params in params_dict['sum'].items():
        #   if lvl==1:
        #       lvl_ex_mu = ex_mu
        #       lvl_ex_var = ex_var
        #   else:
        #       lvl_ex_mu, lvl_ex_var = self.aggregate_seq_(
        #           unnormalize(ex_mu, norm_dict['sum'][1]),
        #           unnormalize(ex_var, norm_dict['sum'][1]**2),
        #           lvl
        #       )
        #       lvl_ex_mu, _ = normalize(lvl_ex_mu, norm_dict['sum'][lvl])
        #       lvl_ex_var, _ = normalize(lvl_ex_var, norm_dict['sum'][lvl]**2)
        #   lvl_loss = self.KL(
        #       params_dict['sum'][lvl][0].detach().numpy(),
        #       params_dict['sum'][lvl][1].detach().numpy()**2,
        #       lvl_ex_mu, lvl_ex_var, lvl
        #   )
        #   opt_loss += lvl_loss

        objective = cp.Minimize(opt_loss)

        #constraints = [ex_preds>=0]

        prob = cp.Problem(objective)#, constraints)

        try:
            opt_loss = prob.solve()
        except cp.error.SolverError:
            opt_loss = prob.solve(solver='SCS')

        #if ex_preds.value is None:

        ex_var_np = ex_var.value
        ex_var_np = np.maximum(ex_var_np, np.ones_like(ex_var_np)*1e-9)

        #import ipdb
        #ipdb.set_trace()

        return ex_mu.value, np.sqrt(ex_var_np)


    def forward(self, feats_in_dict, inputs_dict, feats_tgt_dict, norm_dict, targets_dict=None):
        '''
        inputs_dict: [aggregation method][level]
        norm_dict: [aggregation method][level]
        '''

        norm_dict_np = dict()
        for agg_method in norm_dict.keys():
            norm_dict_np[agg_method] = dict()
            for lvl in norm_dict[agg_method].keys():
                norm_dict_np[agg_method][lvl] = norm_dict[agg_method][lvl].detach().numpy()

        params_dict = dict()
        for agg_method in self.base_models_dict.keys():
            params_dict[agg_method] = dict()
            if agg_method in self.agg_methods:
                for level in self.K_list:
                    model = self.base_models_dict[agg_method][level]
                    inputs = inputs_dict[agg_method][level]
                    feats_in, feats_tgt = feats_in_dict[agg_method][level], feats_tgt_dict[agg_method][level]
                    means, stds = model(feats_in.to(self.device), inputs.to(self.device), feats_tgt.to(self.device))

                    means = means.cpu()
                    if stds is not None:
                        stds = stds.cpu()

                    #if level==1:
                    #   tl = stds.shape[1]
                    #   stds[:, tl//2:, :] += torch.unsqueeze(
                    #       torch.unsqueeze(torch.linspace(1, 0, tl//2), 0),
                    #       -1
                    #   )
    
                    if targets_dict is not None and level != 1:
                        means = targets_dict[agg_method][level]

                    if model.point_estimates:
                        stds = torch.ones_like(means)
                    params = [means, stds]
                    params_dict[agg_method][level] = params

        all_preds_mu, all_preds_std = [], []
        for i in range(params_dict[self.agg_methods[0]][1][0].size()[0]):
            if i%100==0:
                print(i)
            ex_params_dict = dict()
            ex_norm_dict = dict()
            for agg_method in params_dict.keys():
                ex_params_dict[agg_method] = dict()
                ex_norm_dict[agg_method] = dict()
                for lvl in params_dict[agg_method].keys():
                    ex_params_dict[agg_method][lvl] = [params_dict[agg_method][lvl][0][i], params_dict[agg_method][lvl][1][i]]
                    ex_norm_dict[agg_method][lvl] = norm_dict_np[agg_method][lvl][i]

            #import ipdb
            #ipdb.set_trace()
            ex_mu_opt, ex_std_opt = self.optimize(ex_params_dict, ex_norm_dict)
            all_preds_mu.append(ex_mu_opt)
            all_preds_std.append(ex_std_opt)

        all_preds_mu = torch.FloatTensor(all_preds_mu)
        all_preds_std = torch.FloatTensor(all_preds_std)

        #all_preds, _ = normalize(all_preds, norm_dict[0])

        return all_preds_mu, all_preds_std 


class WAVELET(torch.nn.Module):
    """docstring for WAVELET"""
    def __init__(self, wavelet_levels, base_models_dict):
        '''
        base_models_dict (dict) : Dictionary of base models for each level
        wavelet_levels (int) : Number of wavelet levels
        '''
        super(WAVELET, self).__init__()
        self.base_models_dict = base_models_dict
        self.wavelet_levels = wavelet_levels
        
    def forward(self, inputs_dict, norm_dict, targets_dict=None):
        all_levels_preds = []
        for lvl in range(2, self.wavelet_levels+3):
            lvl_preds, _ = self.base_models_dict['wavelet'][lvl](inputs_dict['wavelet'][lvl])
            lvl_preds = unnormalize(lvl_preds, norm_dict['wavelet'][lvl])
            lvl_preds = lvl_preds.detach().numpy()
            all_levels_preds.append(lvl_preds)

        all_levels_preds = [sqz(x) for x in reversed(all_levels_preds)]
        all_preds = pywt.waverec(all_levels_preds, 'haar', mode='periodic')
        all_preds = expand(all_preds)

        all_preds = torch.FloatTensor(all_preds)
        all_preds, _ = normalize(all_preds, norm_dict['wavelet'][1])

        return all_preds, None
