import argparse
import os
import time

from helpers.utils import (
    check_dirs_exist,
    get_device,
    accuracy,
    set_seeds,
    load_model,
    Logger
)
from helpers import dataset
import models
from helpers.trainer import Trainer
from helpers.quantizer import PostQuantizer

from tensorboardX import SummaryWriter
import torch
import torch.optim as optim
import torch.nn as nn


parser = argparse.ArgumentParser(description="Quantize Process")
parser.add_argument('--n-epochs', default=20, type=int)
parser.add_argument('--batch-size', type=int, default=128)
parser.add_argument('--lr', type=float, default=0.001)
parser.add_argument('--seed', type=int, default=111)
parser.add_argument('--model', type=str, default='resnet56')
parser.add_argument('--dataset', type=str, default='cifar10')
parser.add_argument('--load-model-path', type=str, default='None')
parser.add_argument('--quan-mode', type=str, default='all-quan')  # pattern: "(all|conv|fc)-quan"
parser.add_argument('--quan-bits', type=int, default='None')
parser.add_argument('--schedule', type=int, nargs='+', default=[50, 100, 150])
parser.add_argument('--lr-drops', type=float, nargs='+', default=[0.1, 0.1, 0.1])
parser.add_argument('--momentum', default=0.9, type=float)
parser.add_argument('--weight-decay', default=5e-4, type=float)
parser.add_argument('--dev-idx', type=int, default=0)  # The index of the used cuda device
args = parser.parse_args()

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'  # For Mac OS
args.save_dir = f'saves/{int(time.time())}'
args.log_dir = f'{args.save_dir}/log'
args.log_path = f'saves/logs.txt'


class QuantizedModelTrainer(Trainer):
    def __init__(self, writer, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.writer = writer
        self.cross_entropy = nn.CrossEntropyLoss()

        quantizer = PostQuantizer(self.args.quan_mode, device=self.device)
        quantizer.quantize(self.model, self.args.quan_bits)
        self.quan_dict = quantizer.get_quan_dict()

        self.mask = dict()

    def _set_quan_weight_grad(self):
        for name, module in self.model.named_modules():
            if name in self.quan_dict:
                if name not in self.mask:
                    self.mask[name] = dict()
                mask = self.mask[name]
                weight = module.weight
                grad = module.weight.grad

                # Mask gradients of pruend weights
                key = 'grad'
                if key not in mask:
                    mask[key] = torch.where(weight == 0, 0, 1)
                grad *= mask[key]

                # Set gradients of quantized weights
                quan_labels = self.quan_dict[name]
                quan_range = len(torch.unique(quan_labels))
                key = 'ind'
                if key not in mask:
                    mask[key] = dict()
                for i in range(quan_range):
                    if i not in mask[key]:
                        mask[key][i] = torch.where(quan_labels == i)
                    group_ind = mask[key][i]
                    group_grad_sum = torch.sum(grad[group_ind])
                    grad[group_ind] = group_grad_sum
                module.weight.grad.data = grad

    def _get_loss_and_backward(self, batch):
        input, target = batch
        logit = self.model(input)
        loss = self.cross_entropy(logit, target)
        loss.backward()
        self._set_quan_weight_grad()
        top1, top5 = accuracy(logit, target, topk=(1, 5))
        self.writer.add_scalars(
            'data/scalar_group', {
                'total_loss': loss.item(),
                'lr': self.cur_lr,
                'top1': top1,
                'top5': top5
            }, self.global_step
        )
        return loss, top1, top5

    def _evaluate(self, batch):
        input, target = batch
        logit = self.model(input)
        loss = self.cross_entropy(logit, target)
        top1, top5 = accuracy(logit, target, topk=(1, 5))
        return {'loss': loss, 'top1': top1, 'top5': top5}


def main():
    set_seeds(args.seed)
    check_dirs_exist([args.save_dir])
    logger = Logger(args.log_path)
    device = get_device(args.dev_idx)
    if args.dataset not in dataset.__dict__:
        raise NameError
    if args.model not in models.__dict__:
        raise NameError
    logger.log_line()
    train_loader, eval_loader, num_classes = dataset.__dict__[args.dataset](args.batch_size)
    model = models.__dict__[args.model](num_classes=num_classes)
    load_model(model, args.load_model_path, logger, device)
    optimizer = optim.SGD(
        model.parameters(), lr=args.lr, momentum=args.momentum, weight_decay=args.weight_decay, nesterov=True
    )
    base_trainer_cfg = (args, model, train_loader, eval_loader, optimizer, args.save_dir, device, logger)
    writer = SummaryWriter(log_dir=args.log_dir)  # For tensorboardX
    trainer = QuantizedModelTrainer(writer, *base_trainer_cfg)
    logger.log('\n'.join(map(str, vars(args).items())))
    trainer.train()
    print(f'Log Path : {args.log_path}')


if __name__ == '__main__':
    main()
