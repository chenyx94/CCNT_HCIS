from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from abc import ABC

from absl import app
from absl import flags
from absl import logging
import tensorflow as tf

from open_spiel.python import policy
from open_spiel.python import rl_environment
from open_spiel.python.algorithms import exploitability
from open_spiel.python.algorithms import nfsp
from open_spiel.python.algorithms import random_agent
import arm_tf
import numpy as np
import matplotlib.pyplot as plt
import datetime
import time
import nfsp_arm
import pickle

FLAGS = flags.FLAGS

flags.DEFINE_integer("num_train_episodes", int(15e6),  # 15e6
                     "Number of training episodes.")
flags.DEFINE_integer("eval_every", 10000,
                     "Episode frequency at which the agents are evaluated.")
# flags.DEFINE_list("hidden_layers_sizes", [
#     128, 64
# ], "Number of hidden units in the avg-net and Q-net.")
flags.DEFINE_list("hidden_layers_sizes", [
    128, 128, 64
], "Number of hidden units in the avg-net and Q-net.")
flags.DEFINE_integer("replay_buffer_capacity", int(3000),
                     "Size of the replay buffer.")
flags.DEFINE_integer("reservoir_buffer_capacity", int(2e6),
                     "Size of the reservoir buffer.")
flags.DEFINE_float("anticipatory_param", 0.5,  # 0.1 origin
                   "Prob of using the rl best response as episode policy.")


class NFSPPolicies(policy.Policy, ABC):
    """Joint policy to be evaluated."""

    def __init__(self, env, nfsp_policies, mode):
        game = env.game
        player_ids = [0, 1]
        super(NFSPPolicies, self).__init__(game, player_ids)
        self._policies = nfsp_policies
        self._mode = mode
        self._obs = {"info_state": [None, None], "legal_actions": [None, None]}

    def action_probabilities(self, state, player_id=None):
        cur_player = state.current_player()
        legal_actions = state.legal_actions(cur_player)

        self._obs["current_player"] = cur_player
        self._obs["info_state"][cur_player] = (
            state.information_state_tensor(cur_player))
        self._obs["legal_actions"][cur_player] = legal_actions

        info_state = rl_environment.TimeStep(
            observations=self._obs, rewards=None, discounts=None, step_type=None)

        # if cur_player == 0:
        with self._policies[cur_player].temp_mode_as(self._mode):
            p = self._policies[cur_player].step(info_state, is_evaluation=True).probs
            prob_dict = {action: p[action] for action in legal_actions}
        # else:
        #     p = self._policies[cur_player].step(info_state, is_evaluation=True).probs

        # print(prob_dict)
        return prob_dict


class ARMPolicies(policy.Policy, ABC):
    """Joint policy to be evaluated."""

    def __init__(self, env, arm_policies, mode):
        game = env.game
        player_ids = [0, 1]
        super(ARMPolicies, self).__init__(game, player_ids)
        self._policies = arm_policies
        self._mode = mode
        self._obs = {"info_state": [None, None], "legal_actions": [None, None]}

    def action_probabilities(self, state, player_id=None):
        cur_player = state.current_player()
        legal_actions = state.legal_actions(cur_player)

        self._obs["current_player"] = cur_player
        self._obs["info_state"][cur_player] = (
            state.information_state_tensor(cur_player))
        self._obs["legal_actions"][cur_player] = legal_actions

        info_state = rl_environment.TimeStep(
            observations=self._obs, rewards=None, discounts=None, step_type=None)

        p = self._policies[cur_player].step(info_state, is_evaluation=True).probs
        prob_dict = {action: p[action] for action in legal_actions}
        return prob_dict


def eval_against_random_bots(env, trained_agents, random_agents, num_episodes):
    """Evaluates `trained_agents` against `random_agents` for `num_episodes`."""
    num_players = len(trained_agents)
    sum_episode_rewards = np.zeros(num_players)
    win_rate = np.zeros(num_players)
    for player_pos in range(num_players):
        count_vic = 0
        cur_agents = random_agents[:]
        cur_agents[player_pos] = trained_agents[player_pos]
        for _ in range(num_episodes):
            time_step = env.reset()
            episode_rewards = 0
            while not time_step.last():
                player_id = time_step.observations["current_player"]
                if player_pos == player_id:
                    with cur_agents[player_id].temp_mode_as(nfsp_arm.MODE.average_policy):
                        agent_output = cur_agents[player_id].step(time_step, is_evaluation=True)
                else:
                    agent_output = cur_agents[player_id].step(time_step, is_evaluation=True)
                action_list = [agent_output.action]
                time_step = env.step(action_list)
                episode_rewards += time_step.rewards[player_pos]
            if episode_rewards > 0:
                episode_rewards = 1.0
                count_vic = count_vic + 1
            elif episode_rewards < 0:
                episode_rewards = -1.0
            elif episode_rewards == 0:
                episode_rewards = 0
                count_vic = count_vic + 1
            sum_episode_rewards[player_pos] += episode_rewards
            win_rate[player_pos] = count_vic
    return sum_episode_rewards / num_episodes, win_rate / num_episodes


def eval_against_random_bots_nfsp_arm(env, trained_agents, random_agents, num_episodes):
    """Evaluates `trained_agents` against `random_agents` for `num_episodes`."""
    num_players = len(trained_agents)
    sum_episode_rewards = np.zeros(num_players)
    for player_pos in range(num_players):
        cur_agents = random_agents[:]
        cur_agents[player_pos] = trained_agents[player_pos]
        for _ in range(num_episodes):
            time_step = env.reset()
            episode_rewards = 0
            while not time_step.last():
                player_id = time_step.observations["current_player"]
                if hasattr(cur_agents[player_id], 'tag') and cur_agents[player_id].tag() == "nfsp_arm":
                    with cur_agents[player_id].temp_mode_as(nfsp_arm.MODE.average_policy):
                        agent_output = cur_agents[player_id].step(time_step, is_evaluation=True)
                else:
                    with cur_agents[player_id].temp_mode_as(nfsp.MODE.average_policy):
                        agent_output = cur_agents[player_id].step(time_step, is_evaluation=True)
                action_list = [agent_output.action]
                time_step = env.step(action_list)
                episode_rewards += time_step.rewards[player_pos]
            sum_episode_rewards[player_pos] += episode_rewards
    return sum_episode_rewards / num_episodes


def main(unused_argv):
    start_time = time.time()
    game = "leduc_poker"
    num_players = 2

    env_configs = {"players": num_players}
    env = rl_environment.Environment(game, **env_configs)
    # env = rl_environment.Environment(game)
    info_state_size = env.observation_spec()["info_state"][0]
    num_actions = env.action_spec()["num_actions"]
    # print("111111111111111111111111111111111111111: ", info_state_size, num_actions)

    hidden_layers_sizes = [int(l) for l in FLAGS.hidden_layers_sizes]

    # kwargs = {
    #     "replay_buffer_capacity": FLAGS.replay_buffer_capacity,
    #     "epsilon_decay_duration": FLAGS.num_train_episodes,
    #     "epsilon_start": 0.06,
    #     "epsilon_end": 0.001,
    # }

    # nfsp_arm
    kwargs = {
        "replay_buffer_capacity": FLAGS.replay_buffer_capacity,
        "epsilon_decay_duration": FLAGS.num_train_episodes,
        "epsilon_start": 0.06,
        "epsilon_end": 0.001,
    }

    # exploit = []

    with tf.compat.v1.Session() as sess:
        # pylint: disable=g-complex-comprehension
        # test
        agents = [
            nfsp_arm.NFSP(sess, idx, info_state_size, num_actions, hidden_layers_sizes,
                          FLAGS.reservoir_buffer_capacity, FLAGS.anticipatory_param,
                          **kwargs) for idx in range(num_players)
        ]

        # origin nfsp
        # nfsp_agents = [
        #     nfsp.NFSP(sess, idx, state_representation_size=info_state_size, num_actions=num_actions,
        #               hidden_layers_sizes=hidden_layers_sizes,
        #               reservoir_buffer_capacity=FLAGS.reservoir_buffer_capacity,
        #               anticipatory_param=FLAGS.anticipatory_param,
        #               **kwargs) for idx in range(num_players)
        # ]
        # add arm
        # arm_agents = [arm_tf.ARM(sess, idx, info_state_size, num_actions, hidden_layers_sizes, 1000,
        #                      **kwargs) for idx in range(num_players)]

        # agents_tst = [arm_tf_tst.ARM(sess, idx, info_state_size, num_actions, hidden_layers_sizes, 1000,
        #                      **kwargs) for idx in range(num_players)]
        # agents = [arm_tf.ARM(sess, idx, info_state_size, num_actions, hidden_layers_sizes, 1000,
        #                      **kwargs) for idx in range(num_players)]

        expl_policies_avg = NFSPPolicies(env, agents, nfsp_arm.MODE.average_policy)

        # expl_policies_avg_1 = NFSPPolicies(env, nfsp_arm_agents, nfsp_arm.MODE.average_policy)
        # add arm
        # expl_policies_avg_1 = ARMPolicies(env, arm_agents, arm_tf.MODE.average_policy)

        sess.run(tf.global_variables_initializer())
        saver = tf.train.Saver()

        exploit = []
        exploit_iter = []
        exploit_arm = []
        print('start training nsfp_arm...')
        file = 'nsfp_agents_1_15_nfsp_arm'
        step_counter = 0
        saver.save(sess, "./Model_NFSP_ARM_LD_1116/model_init.ckpt")
        for ep in range(FLAGS.num_train_episodes):
            arm_prev_timestep = None
            arm_prev_action = None

            ###  if replay buffer is full, update nfsp_arm's rl and sl
            min_buf = min(agents[0].rl_buffer_len, agents[1].rl_buffer_len)
            if min_buf > 4000:
                step_counter += 1
                for agent in agents:
                    agent.learn()
                # explo = exploitability.nash_conv(env.game, expl_policies_avg)
                # print("Iteration {} NFSP_ARM Exploitability: {}".format(step_counter, explo))
                # exploit_iter.append(explo)
                # with open("log/{}_iter_ex.txt".format(file), 'a+') as f:
                #     f.write("{}\n".format(explo))
            ###
            if (ep + 1) % 500000 == 0:
                saver.save(sess, "./Model_NFSP_ARM_LD_1116/model.ckpt", global_step=ep + 1)

                with open('./Model_NFSP_ARM_LD_1116/agent_0' + str(ep + 1) + '.pkl', 'wb') as f:
                    pickle.dump(agents[0].cum_prob, f, pickle.HIGHEST_PROTOCOL)

                with open('./Model_NFSP_ARM_LD_1116/agent_1' + str(ep + 1) + '.pkl', 'wb') as f:
                    pickle.dump(agents[1].cum_prob, f, pickle.HIGHEST_PROTOCOL)

            if (ep + 1) % FLAGS.eval_every == 0:
                ep_time = time.time()
                cost = ep_time - start_time
                print("episode: {}, time: {}".format(ep + 1, cost))
                losses = [agent.loss for agent in agents]
                # q_pluses = [agent.q_plus for agent in nfsp_agents]
                # q_pluses_all = [agent.q_plus_all for agent in nfsp_agents]
                print("Losses: {}".format(losses))
                # print("Advantages: {}".format(q_pluses_all))
                expl = exploitability.exploitability(env.game, expl_policies_avg)
                # add arm
                expl_arm = None
                # expl_arm = exploitability.exploitability(env.game, expl_policies_avg_1)
                print("{} NFSP_ARM Exploitability AVG {}".format(ep + 1, expl))
                # print("{} ARM Exploitability AVG {}".format(ep + 1, expl_arm))
                random_agents = [
                    random_agent.RandomAgent(player_id=idx, num_actions=num_actions)
                    for idx in range(num_players)
                ]
                r_mean = eval_against_random_bots(env, agents, random_agents, 1000)
                print("Against Random_Bots: ", r_mean)
                print("_____________________________________________")
                exploit.append(expl)
                exploit_arm.append(expl_arm)
                with open("log/{}_loss.txt".format(file), 'a+') as f:
                    f.write("{}: {}\n".format(cost, losses))
                with open("log/{}_ex.txt".format(file), 'a+') as f:
                    f.write("{}, {}: {}\n".format(step_counter, cost, expl))
                with open("log/{}_ex(arm).txt".format(file), 'a+') as f:
                    f.write("{}: {}\n".format(cost, r_mean))

                # with open("log/{}_q_plus.txt".format(file), 'a+') as f:
                #     f.write("{}\n".format(q_pluses))
                # with open("log/{}_q_plus_all.txt".format(file), 'a+') as f:
                #     f.write("{}\n".format(q_pluses_all))

            time_step = env.reset()
            while not time_step.last():
                # print("in game")
                player_id = time_step.observations["current_player"]
                agent_output = agents[player_id].step(time_step)
                prev_time_step = time_step
                prev_action = agent_output.action
                action_list = [agent_output.action]
                # print(action_list)
                time_step = env.step(action_list)
                # if arm_prev_timestep:
                #     arm_agents[player_id].add_transition(arm_prev_timestep, arm_prev_action, prev_time_step)
                arm_prev_timestep = prev_time_step
                arm_prev_action = prev_action

            # Episode is over, step all agents with final info state.
            for agent in agents:
                agent.step(time_step)

            # for agent in arm_agents:
            #     agent.add_transition(arm_prev_timestep, arm_prev_action, time_step)
            #
            # for agent in arm_agents:
            #     # print(agent.player_id, len(agent._replay_buffer))
            #     arm_loss = agent.learn()
            #     if arm_loss is not None:
            #         # print("")
            #         # print("{}: [{}]{}".format(ep, agent.player_id, arm_loss))
            #         agent._replay_buffer.clear()

        plt.figure()
        plt.plot(exploit)
        plt.plot(exploit_iter)
        plt.show()

        saver.save(sess, "./Model_NFSP_ARM_1218/model.ckpt")

        with open('./Model_NFSP_ARM_1218/agent_0.pkl', 'wb') as f:
            pickle.dump(agents[0].cum_prob, f, pickle.HIGHEST_PROTOCOL)

        with open('./Model_NFSP_ARM_1218/agent_1.pkl', 'wb') as f:
            pickle.dump(agents[1].cum_prob, f, pickle.HIGHEST_PROTOCOL)

        # for i in range(1000):
        #     r_mean = eval_against_random_bots_nfsp_arm(env, nfsp_agents, arm_agents, 1000)
        #     logging.info("Mean episode rewards: %s, ", r_mean)
        #     with open('log/{}_nfsp1_vs_arm2.txt'.format(file), 'a+') as f:
        #         f.write('{}\n'.format(r_mean[0]))
        #     with open('log/{}_arm1_vs_nfsp2.txt'.format(file), 'a+') as f:
        #         f.write('{}\n'.format(r_mean[1]))

        # exploit = []
        # r_tst_p1 = []
        # r_tst_p2 = []
        # print('start training nsfp_arm...')
        # file = 'nsfp_arm_agents_8-11'
        # for ep in range(FLAGS.num_train_episodes):
        #     if (ep + 1) % FLAGS.eval_every == 0:
        #         losses = [agent.loss for agent in nfsp_arm_agents]
        #         q_pluses = [agent.q_plus for agent in nfsp_arm_agents]
        #         q_pluses_all = [agent.q_plus_all for agent in nfsp_arm_agents]
        #         print("Losses: {}".format(losses))
        #         print("Advantages: {}".format(q_pluses_all))
        #         expl = exploitability.exploitability(env.game, expl_policies_avg_1)
        #         print("{} Exploitability AVG {}".format(ep + 1, expl))
        #         print("_____________________________________________")
        #         exploit.append(expl)
        #         with open("log/{}_loss.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(losses))
        #         with open("log/{}_ex.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(expl))
        #
        #         with open("log/{}_q_plus.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(q_pluses))
        #         with open("log/{}_q_plus_all.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(q_pluses_all))
        #
        #     time_step = env.reset()
        #     while not time_step.last():
        #         player_id = time_step.observations["current_player"]
        #         agent_output = nfsp_arm_agents[player_id].step(time_step)
        #         action_list = [agent_output.action]
        #         time_step = env.step(action_list)
        #
        #     # Episode is over, step all agents with final info state.
        #     for agent in nfsp_arm_agents:
        #         agent.step(time_step)

        # if (ep + 1) % 1000 == 0:
        #     # expl = exploitability.exploitability(env.game, expl_policies_avg)
        #     # exploit.append(expl)
        #     # with open("log/tic_ex_arm_vs_nfsp_{}.txt".format(agent_type), "a+") as f:
        #     #     f.write("{}\n".format(expl))
        #     random_agents = [
        #         random_agent.RandomAgent(player_id=idx, num_actions=num_actions)
        #         for idx in range(num_players)
        #     ]
        #     r_mean = eval_against_random_bots_nfsp_arm(env, nfsp_arm_agents, random_agents, 1000)
        #     logging.info("Mean episode rewards: %s, ", r_mean)
        #     r_tst_p1.append(r_mean[0])
        #     r_tst_p2.append(r_mean[1])
        # plt.plot(exploit)
        # plt.show()
        # plt.plot(r_tst_p1, label="player1_nfps_arm")
        # plt.plot(r_tst_p2, label="player2_nfsp_arm")
        # plt.legend()
        # plt.show()
        # exploit = []
        # # print(FLAGS.num_train_episodes)
        # print('start training arm...')
        # file = 'arm_agents_v7'
        # for ep in range(FLAGS.num_train_episodes):
        #     if (ep + 1) % FLAGS.eval_every == 0:
        #         losses = [agent.loss for agent in arm_agents]
        #         q_pluses = [agent.q_plus for agent in arm_agents]
        #         q_pluses_all = [agent.q_plus_all for agent in arm_agents]
        #         print("Losses: {}".format(losses))
        #         # print("Advantages: {}".format(q_pluses_all))
        #         expl = exploitability.exploitability(env.game, expl_policies_avg_1)
        #         print("{} Exploitability AVG {}".format(ep + 1, expl))
        #         print("_____________________________________________")
        #         exploit.append(expl)
        #         with open("log/{}_loss_arm.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(losses))
        #         with open("log/{}_ex_arm.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(expl))
        #
        #         with open("log/{}_q_plus.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(q_pluses))
        #         with open("log/{}_q_plus_all.txt".format(file), 'a+') as f:
        #             f.write("{}\n".format(q_pluses_all))
        #
        #     time_step = env.reset()
        #     while not time_step.last():
        #         player_id = time_step.observations["current_player"]
        #         agent_output = arm_agents[player_id].step(time_step)
        #         action_list = [agent_output.action]
        #         time_step = env.step(action_list)
        #
        #     # Episode is over, step all agents with final info state.
        #     for agent in arm_agents:
        #         agent.step(time_step)
        # plt.plot(exploit)
        # plt.show()

        # exploit = []
        # r = []
        # #
        # print('start training nfsp arm and nfsp...')
        # for i in range(1000):
        #     r_mean = eval_against_random_bots_nfsp_arm(env, nfsp_agents, nfsp_arm_agents, 1000)
        #     logging.info("Mean episode rewards: %s, ", r_mean)
        #     with open('log/main_nfsp_arm_0_vs_nfsp1_v1.txt', 'a+') as f:
        #         f.write('{}\n'.format(r_mean[0]))
        #     with open('log/main_nfsp0_vs_nfsp_arm_1_v1.txt', 'a+') as f:
        #         f.write('{}\n'.format(r_mean[1]))

        # for ep in range(FLAGS.num_train_episodes):
        #     if (ep + 1) % FLAGS.eval_every == 0:
        #         losses = [agent.loss for agent in agents]
        #         q_pluses = [agent.q_plus for agent in agents]
        #         q_pluses_all = [agent.q_plus_all for agent in agents]
        #         print("Losses: {}".format(losses))
        #         # print("Advantages: {}".format(q_pluses_all))
        #         expl = exploitability.exploitability(env.game, expl_policies_avg)
        #         print("{} Exploitability AVG {}".format(ep + 1, expl))
        #         print("_____________________________________________")
        #         exploit.append(expl)
        #         with open("log/loss_arm.txt", 'a+') as f:
        #             f.write("{}\n".format(losses))
        #         with open("log/ex_arm.txt", "a+") as f:
        #             f.write("{}\n".format(expl))
        #         with open("log/q_plus.txt", 'a+') as f:
        #             f.write("{}\n".format(q_pluses))
        #         with open("log/q_plus_all.txt", 'a+') as f:
        #             f.write("{}\n".format(q_pluses_all))
        #
        #     time_step = env.reset()
        #     while not time_step.last():
        #         player_id = time_step.observations["current_player"]
        #         agent_output = agents[player_id].step(time_step)
        #         action_list = [agent_output.action]
        #         time_step = env.step(action_list)
        #
        #     # Episode is over, step all agents with final info state.
        #     for agent in agents:
        #         agent.step(time_step)


if __name__ == "__main__":
    app.run(main)
