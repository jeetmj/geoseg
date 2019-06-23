#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
  @Email:  guangmingwu2010@gmail.com
  @Copyright: go-hiroaki
  @License: MIT
"""
import os
import time
import metrics
import losses
import pandas as pd
import warnings

import torch
from torch.utils.data import DataLoader

warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)


Src_DIR = os.path.dirname(os.path.abspath(__file__))
Logs_DIR = os.path.join(Src_DIR, '../logs')
Checkpoint_DIR = os.path.join(Src_DIR, '../checkpoint')
if not os.path.exists(Logs_DIR):
    os.mkdir(Logs_DIR)
    os.mkdir(os.path.join(Logs_DIR, 'raw'))
    os.mkdir(os.path.join(Logs_DIR, 'curve'))
    # os.mkdir(os.path.join(Logs_DIR, 'snapshot'))

if not os.path.exists(Checkpoint_DIR):
    os.mkdir(Checkpoint_DIR)


def load_checkpoint(name, cuda=False, eval=False):
    assert os.path.exists("{}/{}".format(Checkpoint_DIR, name)
                          ), "{} not exists.".format(name)
    print("Loading checkpoint: {}".format(name))
    model = torch.load("{}/{}".format(Checkpoint_DIR, name))
    if cuda:
        model.cuda()
    if eval:
        model.eval()
    return model


class Base(object):
    def __init__(self, args, method, is_multi=False):
        self.args = args
        self.method = method
        self.is_multi = is_multi
        self.date = time.strftime("%h%d_%H")
        self.repr = "{}_{}_{}".format(
            self.method, self.args.trigger, self.args.terminal)
        self.epoch = 0
        self.iter = 0
        self.logs = []
        self.criterion = losses.BCELoss()
        self.evaluator = metrics.OAAcc()
        # self.snapshot = os.path.join(Logs_DIR, "snapshot", self.method)
        # if not os.path.exists(self.snapshot):
        #     os.makedirs(self.snapshot)
        # else:
        #     shutil.rmtree(self.snapshot)
        #     os.makedirs(self.snapshot)
        
        self.header = ["epoch", "iter"]
        for stage in ['trn', 'val']:
            for key in [repr(self.criterion),repr(self.evaluator),"FPS"]:
                self.header.append("{}_{}".format(stage, key))

    def logging(self, verbose=True):
        self.logs.append([self.epoch, self.iter] +
                         self.trn_log + self.val_log)
        if verbose:
            str_a = ['{}:{:05d}'.format(k,v) for k,v in zip(self.header[:2], [self.epoch, self.iter])]
            str_b = ['{}:{:.2f}'.format(k,v) for k,v in zip(self.header[2:], self.trn_log + self.val_log)]
            print(' ,'.join(str_a + str_b))

    def save_log(self):
        self.logs = pd.DataFrame(self.logs,
                                 columns=self.header)
        self.logs.to_csv(os.path.join(Logs_DIR, 'raw', '{}.csv'.format(self.repr)), index=False, float_format='%.3f')

        speed_info = [self.repr, self.logs.iloc[:, 4].mean(), self.logs.iloc[:, 7].mean()]
        df = pd.DataFrame([speed_info],
                          columns=["experiment", self.header[4], self.header[7]])
        if os.path.exists(os.path.join(Logs_DIR, 'speed.csv')):
            prev_df = pd.read_csv(os.path.join(Logs_DIR, 'speed.csv'))
            df = prev_df.append(df)
        df.to_csv(os.path.join(Logs_DIR, 'speed.csv'), index=False)

    def save_checkpoint(self, net):
        for name, model in zip(net.names, net.models):
            torch.save(model.state_dict(), 
                       os.path.join(Checkpoint_DIR, "{}-{}.pth".format(self.repr, name)))

    def learning_curve(self, idxs=[2,3,5,6]):
        import seaborn as sns
        import matplotlib.pyplot as plt
        plt.switch_backend('agg')
        # set style
        sns.set_context("paper", font_scale=1.5,)
        # sns.set_style("ticks", {
        #     "font.family": "Times New Roman",
        #     "font.serif": ["Times", "Palatino", "serif"]})

        for idx in idxs:
            plt.plot(self.logs[self.args.trigger],
                     self.logs[self.header[idx]], label=self.header[idx])
        plt.ylabel(" {} / {} ".format(repr(self.criterion), repr(self.evaluator)))
        if self.args.trigger == 'epoch':
            plt.xlabel("Epochs")
        else:
            plt.xlabel("Iterations")
        plt.suptitle("Training log of {}".format(self.method))
        # remove top&left line
        # sns.despine()
        plt.legend(bbox_to_anchor=(1.01, 1), loc=2, borderaxespad=0.)
        plt.savefig(os.path.join(Logs_DIR, 'curve', '{}.png'.format(self.repr)),
                    format='png', bbox_inches='tight', dpi=300)
        # plt.savefig(os.path.join(Logs_DIR, 'curve', '{}.eps'.format(self.repr)),
        #             format='eps', bbox_inches='tight', dpi=300)
        return 0


class stackTrainer(Base):
    def training(self, net, datasets):
        """
          Args:
            net: (object) basic models & optimizer
            datasets : (list) [train, val] dataset object
        """
        args = self.args
        best_trn_perform, best_val_perform = -1, -1
        steps = len(datasets[0]) // args.batch_size
        if steps * args.batch_size < len(datasets[0]):
            steps += 1

        if args.trigger == 'epoch':
            args.epochs = args.terminal
            args.iters = steps * args.terminal
            args.iter_interval = steps * args.interval
        else:
            args.epochs = args.terminal // steps + 1
            args.iters = args.terminal
            args.iter_interval = args.interval

        for model in net.models:
            model.train()
        trn_loss, trn_acc = [], []
        start = time.time()
        AL = losses.AlignLoss()
        for epoch in range(1, args.epochs + 1):
            self.epoch = epoch
            # setup data loader
            data_loader = DataLoader(datasets[0], args.batch_size, num_workers=4,
                                     shuffle=True, pin_memory=True,)
            for idx, (x, y) in enumerate(data_loader):
                self.iter += 1
                if self.iter > args.iters:
                    self.iter -= 1
                    break
                # get tensors from sample
                if args.cuda:
                    x = x.cuda()
                    y = y.cuda()
                # forwarding
                gen_ys = []
                for model in net.models:
                    gen_ys.append(model(x))
                loss_align = AL.ALMSE(gen_ys)
                gen_y = sum(gen_ys) / len(gen_ys)
                loss_seg = self.criterion(gen_y, y)
                loss = loss_seg + args.alpha * loss_align
                # update parameters
                net.optimizer.zero_grad()
                loss.backward()
                net.optimizer.step()
                # update taining condition
                trn_loss.append(loss.item())
                trn_acc.append(self.evaluator(gen_y.data, y.data)[0].item())
                # validating
                if self.iter % args.iter_interval == 0:
                    trn_fps = (args.iter_interval * args.batch_size) / (time.time() - start)
                    self.trn_log = [round(sum(trn_loss) / len(trn_loss), 3), 
                                    round(sum(trn_acc) / len(trn_acc), 3),
                                    round(trn_fps, 3)]
 
                    self.validating(net, datasets[1])
                    self.logging(verbose=True)
                    if self.val_log[1] >= best_val_perform:
                        best_trn_perform = self.trn_log[1]
                        best_val_perform = self.val_log[1]
                        checkpoint_info = [self.repr, self.epoch, self.iter,
                                           best_trn_perform, best_val_perform]
                        # save better checkpoint
                        self.save_checkpoint(net)
                    # reinitialize
                    start = time.time()
                    trn_loss, trn_acc = [], []
                    for model in net.models:
                        model.train()
        df = pd.DataFrame([checkpoint_info],
                          columns=["experiment", "best_epoch", "best_iter", self.header[3], self.header[6]])
        if os.path.exists(os.path.join(Checkpoint_DIR, 'checkpoint.csv')):
            prev_df = pd.read_csv(os.path.join(Checkpoint_DIR, 'checkpoint.csv'))
            df = prev_df.append(df)
        df.to_csv(os.path.join(Checkpoint_DIR, 'checkpoint.csv'), index=False)

        print("Best {} Performance: \n".format(repr(self.evaluator)))
        print("\t Trn:", best_trn_perform)
        print("\t Val:", best_val_perform)

    def validating(self, net, dataset):
        """
          Args:
            net: (object) basic models & optimizer
            dataset : (object) dataset
          return [loss, acc]
        """
        args = self.args
        data_loader = DataLoader(dataset, args.batch_size, num_workers=4,
                                 shuffle=False, pin_memory=True,)
        val_loss, val_acc = [], []
        start = time.time()
        for model in net.models:
            model.eval()
        for idx, (x, y) in enumerate(data_loader):
            # get tensors from sample
            if args.cuda:
                x = x.cuda()
                y = y.cuda()
            # forwading
            gen_ys = []
            for model in net.models:
                gen_ys.append(model(x))
            gen_y = sum(gen_ys) / len(gen_ys)

            val_loss.append(self.criterion(gen_y, y).item())
            val_acc.append(self.evaluator(gen_y.data, y.data)[0].item())

        val_fps = (len(val_loss) * args.batch_size ) / (time.time() - start)
        self.val_log = [round(sum(val_loss) / len(val_loss), 3), 
                        round(sum(val_acc) / len(val_acc), 3),
                        round(val_fps, 3)]

