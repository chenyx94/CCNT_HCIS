import numpy as np
import tensorflow as tf
import pickle
import copy
from XFP import XFP, LeducRLEnv
from NFSP import NFSP
import matplotlib.pyplot as plt
import argparse
from math import *

FLAGS = tf.app.flags.FLAGS

parser = argparse.ArgumentParser()
parser.add_argument('--seed', help='seed', default='33')
args = parser.parse_args()

# Experiment settings
tf.app.flags.DEFINE_integer('seed', 33, 'seed')
tf.app.flags.DEFINE_integer('card_num', 7, 'card numbers in leduc')            ## 修改为7
tf.app.flags.DEFINE_integer('batch', 128, 'batch size')
tf.app.flags.DEFINE_float('lr_sl', 0.005, 'learning rate sl')
tf.app.flags.DEFINE_float('lr_rl', 0.1, 'learning rate rl')
tf.app.flags.DEFINE_float('anticipatory', 1, 'anticipatory parameter')
tf.app.flags.DEFINE_float('epsilon', 0.06, 'epsilon greedy start')
tf.app.flags.DEFINE_integer('fsp_iter', 10000000, 'fictitious play iterations')
tf.app.flags.DEFINE_integer('train_frequency', 128, 'training frequency')
tf.app.flags.DEFINE_integer('train_start', 1024, 'training start')
tf.app.flags.DEFINE_integer('rl_len', 200000, 'buffer length for rl')
tf.app.flags.DEFINE_integer('sl_len', 2000000, 'buffer length for sl')
tf.app.flags.DEFINE_integer('refit', 300, 'refit target network')
FLAGS.seed = int(args.seed)

# # 已改写到NFSP中
# xfp = XFP(card_num=FLAGS.card_num, seed=FLAGS.seed)
# exploits = []
# its = []
#
#
# def evaluate_exploitabality(p1, p2):
#     xfp.opponent_policy_p2 = p2
#     xfp.compute_p1_best_response()
#     br1_op = XFP.convert_q_s_a2greedy_policy(xfp.q_value1_final)
#     xfp.opponent_policy_p1 = p1
#     xfp.compute_p2_best_response()
#     br2_op = XFP.convert_q_s_a2greedy_policy(xfp.q_value2_final)
#
#     # compute optimal best response payoff
#     realization_br1_op = XFP.compute_realization(br1_op, p2)
#     realization_br2_op = XFP.compute_realization(p1, br2_op)
#     e = [XFP.compute_payoff_given_realization(realization_br1_op)[0],
#          XFP.compute_payoff_given_realization(realization_br2_op)[1]]
#     exploitability = (e[0] + e[1]) / 2.0
#     xfp.finish()
#     return exploitability, e


def some_tests():
    flags = copy.deepcopy(FLAGS)
    flags.batch = 4
    flags.epoch = 1
    flags.fsp_iter = 10
    flags.epsilon = 0.5
    flags.anticipatory = 0.5
    flags.sl_len = 10
    flags.rl_len = 2
    agent = NFSP(flags)
    while True:
        agent.play_game()
        # agent.sl_replay[0].get_random_batch()
        # agent.sl_replay[1].get_random_batch()
        agent.rl_replay[0].get_random_batch()
        agent.rl_replay[1].get_random_batch()
        # raw_input()


if __name__ == '__main__':
    # some_tests()
    agent = NFSP(FLAGS)
    played_games = 0
    agent.play_game()  ## epsilon
    # while True:
    #     agent.play_game()
    #     agent.epsilon *= 0.99
    #     played_games += 1
    #     # agent.epsilon = FLAGS.epsilon * (1 / sqrt(played_games))
    #     if played_games % 1000 == 0:
    #         policy = agent.compute_self_policy()
    #         exploit, e = evaluate_exploitabality(policy[0], policy[1])
    #         its.append(played_games)
    #         exploits.append(exploit)
    #         print("exploit", exploit)
    #
    #     if played_games % 1000 == 0:
    #         plt.clf()
    #         plt.plot(its, exploits)
    #         fig = 'log/1' + str(FLAGS.seed) + '.jpeg'
    #         plt.savefig(fig)
