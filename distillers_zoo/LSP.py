import torch
import torch.nn as nn
import torch.nn.functional as F


class LogitSimilarity(nn.Module):
    """Multiple layers of similarity-preserving knowledge Distillation"""
    def __init__(self):
        super().__init__()

    def forward(self, s_g_l, t_g_l):
        # --------------------------------------------
        # Shape of s_g_l : ((s_nl,), (bs, s_ch, s_h, s_w))   , (1,)
        # Shape of t_g_l : ((t_nl-1,), (bs, t_ch, t_h, t_w)) , (1,)
        # --------------------------------------------
        s_g, s_l = s_g_l  # Group of student's features, student's logit
        t_g, t_l = t_g_l  # Group of teacher's features, teacher's logit

        s_nl = len(s_g)
        t_nl = len(t_g) + 1

        s_l_mtx = self.get_sim_matrix(s_l, is_at=False)  # (bs, bs)
        t_l_mtx = self.get_sim_matrix(t_l, is_at=False)  # (bs, bs)

        s_g_mtx = torch.stack([self.get_sim_matrix(s_f) for s_f in s_g])  # (s_nl, bs, bs)
        t_g_mtx = torch.stack([self.get_sim_matrix(t_f) for t_f in t_g] + [t_l_mtx])  # (t_nl, bs, bs)

        s_g_mtx = torch.unsqueeze(s_g_mtx, dim=1).repeat(1, t_nl, 1, 1)  # (s_nl, t_nl, bs, bs)
        t_g_mtx = torch.unsqueeze(t_g_mtx, dim=0).repeat(s_nl, 1, 1, 1)  # (s_nl, t_nl, bs, bs)

        l_loss = (s_l_mtx - t_l_mtx).pow(2).mean()  # (1,)
        g_loss = (s_g_mtx - t_g_mtx).pow(2).view(s_nl, -1).mean(1).sum()  # (1,)

        loss = (l_loss + g_loss) / (s_nl + 1)
        return loss

    def get_sim_matrix(self, f, is_at=True):
        # --------------------------------------------
        # Shape of f : (bs, ch, h, w)
        # --------------------------------------------
        if is_at:
            f = self.at(f)
        f = f.view(f.shape[0], -1)  # (bs, ch * h * w) or (bs, h * w)
        f = F.normalize(f, dim=1)  # (bs, ch * h * w)
        mtx = torch.matmul(f, torch.t(f))  # (bs, bs)
        mtx = F.normalize(mtx, dim=1)  # (bs, bs)
        return mtx

    def at(self, f):
        # --------------------------------------------
        # Shape of f : (bs, ch, h, w)
        # --------------------------------------------
        return F.normalize(f.pow(2).mean(1).view(f.size(0), -1))  # (bs, h * w)
