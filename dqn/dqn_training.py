#!/usr/bin/env python3
import tensorflow as tf
import cv2
import sys
import os
import random
import numpy as np
import time
import logging
import matplotlib.pyplot as plt
from skimage.transform import resize
import matplotlib.image as mpimg

from termcolor import colored
from common.util import egreedy, get_action_index, make_movie, load_memory
from common.game_state import get_wrapper_by_name

logger = logging.getLogger("dqn")

ACTION_MEANING = {
    0 : "NOOP",
    1 : "FIRE",
    2 : "UP",
    3 : "RIGHT",
    4 : "LEFT",
    5 : "DOWN",
    6 : "UPRIGHT",
    7 : "UPLEFT",
    8 : "DOWNRIGHT",
    9 : "DOWNLEFT",
    10 : "UPFIRE",
    11 : "RIGHTFIRE",
    12 : "LEFTFIRE",
    13 : "DOWNFIRE",
    14 : "UPRIGHTFIRE",
    15 : "UPLEFTFIRE",
    16 : "DOWNRIGHTFIRE",
    17 : "DOWNLEFTFIRE",
}

try:
    import cPickle as pickle
except ImportError:
    import pickle

class DQNTraining(object):
    def __init__(
        self, sess, network, game_state, resized_height, resized_width, phi_length, batch,
        name, gamma, observe, explore, final_epsilon, init_epsilon, replay_memory,
        update_freq, save_freq, eval_freq, eval_max_steps, copy_freq,
        folder, load_demo_memory=False, demo_memory_folder=None, demo_ids=None,
        load_demo_cam=False, demo_cam_id=None,
        train_max_steps=sys.maxsize, human_net=None, confidence=0., psi=0.999995,
        train_with_demo_steps=0, use_transfer=False, reward_type='CLIP'):
        """ Initialize experiment """
        self.sess = sess
        self.net = network
        self.game_state = game_state
        self.observe = observe
        self.explore = explore
        self.final_epsilon = final_epsilon
        self.init_epsilon = init_epsilon
        self.update_freq = update_freq # backpropagate frequency
        self.save_freq = save_freq
        self.eval_freq = eval_freq
        self.eval_max_steps = eval_max_steps
        self.copy_freq = copy_freq # copy q to t-network frequency
        self.resized_h = resized_height
        self.resized_w = resized_width
        self.phi_length = phi_length
        self.batch = batch
        self.name = name
        self.folder = folder
        self.load_demo_memory = load_demo_memory
        self.demo_memory_folder = demo_memory_folder
        self.demo_ids = demo_ids
        self.load_demo_cam = load_demo_cam
        self.demo_cam_id = demo_cam_id
        self.train_max_steps = train_max_steps
        self.train_with_demo_steps = train_with_demo_steps
        self.use_transfer = use_transfer
        self.reward_type = reward_type

        self.human_net = human_net
        self.confidence = confidence
        self.use_human_advice = False
        self.psi = self.init_psi = psi
        if self.human_net is not None:
            self.use_human_advice = True

        self.replay_memory = replay_memory

        if not os.path.exists(self.folder + '/frames'):
            os.makedirs(self.folder + '/frames')

    def _reset(self, hard_reset=True):
        self.game_state.reset(hard_reset=hard_reset)
        for _ in range(self.phi_length):
            self.replay_memory.add(
                self.game_state.x_t,
                0,
                self.game_state.reward,
                self.game_state.terminal,
                self.game_state.lives,
                fullstate=self.game_state.full_state)

    def _add_demo_experiences(self):
        assert self.demo_memory_folder is not None
        demo_memory, actions_ctr, total_rewards, total_steps = load_memory(
            name=None,
            demo_memory_folder=self.demo_memory_folder,
            demo_ids=self.demo_ids,
            imgs_normalized=False)

        logger.info("Memory size={}".format(self.replay_memory.size))
        logger.info("Adding human experiences...")
        for idx in list(demo_memory.keys()):
            demo = demo_memory[idx]
            for i in range(demo.max_steps):
                self.replay_memory.add(
                    demo.imgs[i], demo.actions[i],
                    demo.rewards[i], demo.terminal[i],
                    demo.lives[i], demo.full_state[i])
            demo.close()
            del demo
        logger.info("Memory size={}".format(self.replay_memory.size))
        time.sleep(2)

    def _load(self):
        if self.net.load():
            # set global step
            self.global_t = self.net.global_t
            logger.info(">>> global step set: {}".format(self.global_t))
            # set wall time
            wall_t_fname = self.folder + '/' + 'wall_t.' + str(self.global_t)
            with open(wall_t_fname, 'r') as f:
                wall_t = float(f.read())
            # set epsilon
            epsilon_fname = self.folder + '/epsilon'
            with open(epsilon_fname, 'r') as f:
                self.epsilon = float(f.read())
            self.rewards = pickle.load(open(self.folder + '/' + self.name.replace('-', '_') + '-dqn-rewards.pkl', 'rb'))
            self.replay_memory.load(name=self.name, folder=self.folder)
        else:
            logger.warn("Could not find old network weights")
            if self.load_demo_memory:
                self._add_demo_experiences()
            self.global_t = 0
            self.epsilon = self.init_epsilon
            self.rewards = {'train':{}, 'eval':{}}
            wall_t = 0.0
        return wall_t

    def visualize(self, conv_output, conv_grad):  # image, gb_viz):
        output = conv_output
        grads_val = conv_grad

        # global average pooling
        weights = np.mean(grads_val, axis=(0, 1))
        cam = np.zeros(output.shape[0:2], dtype=np.float32)
        # cam = np.ones(output.shape[0:2], dtype=np.float32)
        for i, w in enumerate(weights):
            cam += w * output[:,:,i]
        # passing through Relu
        cam = np.maximum(cam, 0) # only care about positive
        cam = cam / np.max(cam) # scale to [0,1]
        cam = resize(cam, (84, 84), preserve_range=True)
        cam_heatmap = cv2.applyColorMap(np.uint8(225*cam), cv2.COLORMAP_JET)
        cam_heatmap = cv2.cvtColor(cam_heatmap, cv2.COLOR_BGR2RGB)

        return cam_heatmap

    def calculate_cam(self, test_cam_si):
        state = []
        action_onehot = []
        action_array = []

        for i in range(len(test_cam_si)):
            readout_t = self.net.evaluate(test_cam_si[i])[0]
            action = get_action_index(readout_t,
                is_random=(random.random() <= 0.05),
                n_actions=self.game_state.env.action_space.n)
            action_array.append(action)
            a_onehot = np.zeros(self.game_state.env.action_space.n)
            a_onehot[action] = 1
            action_onehot.append(a_onehot)

            state.append(np.mean(test_cam_si[i], axis=-1))

        conv_value, conv_grad, gbgrad = self.net.grad_cam(test_cam_si,
                                                          action_onehot)
        cam = []
        img = []

        for i in range(len(conv_value)):
            cam_tmp = self.visualize(conv_value[i], conv_grad[i])
            cam.append(cam_tmp)

            # fake RGB channels for demo images
            state_tmp = cv2.merge((state[i], state[i], state[i]))
            img.append(state_tmp)

        return np.array(cam), np.array(img), action_array

    def test(self, render=False):
        logger.info("Evaluate policy at global_t={}...".format(self.global_t))

        episode_buffer = []
        self.game_state.reset(hard_reset=True)
        episode_buffer.append(self.game_state.get_screen_rgb())

        max_steps = self.eval_max_steps
        total_reward = 0
        total_steps = 0
        episode_reward = 0
        episode_steps = 0
        n_episodes = 0

        # use one demonstration data to record cam
        # only need to make movie for demo data once
        # if self.global_t == 0:
        cam, state, action = self.calculate_cam(self.test_cam_si)
        cam_plus_img = []
        cam_side_img = []

        for i in range(len(cam)):
            # overlay cam-state
            overlay = np.uint8(cam[i]).copy()
            output = np.uint8(state[i]).copy()
            alpha = 0.3
            cv2.addWeighted(overlay, alpha, output, 1 - alpha,
                0, output)
            # create a title space for action
            title_space = np.zeros((20, 84, 3), np.uint8)
            title_space[:] = (255,255,255)
            cv2.putText(title_space, "{}".format(ACTION_MEANING[action[i]]),
                (20, 14), cv2.FONT_HERSHEY_DUPLEX, .4, (0, 0, 0), 1)
            # concate title and state
            vcat_output = cv2.vconcat((title_space, output))
            cam_plus_img.append(vcat_output)

            # side-by-side cam-state
            hcat_cam_state =  cv2.hconcat((np.uint8(cam[i]).copy(),
                                           np.uint8(state[i]).copy()))
            title_space = np.zeros((20, 84*2, 3), np.uint8)
            title_space[:] = (255,255,255)
            vcat_title_camstate = cv2.vconcat((title_space, hcat_cam_state))
            cv2.putText(vcat_title_camstate, "{}".format(ACTION_MEANING[action[i]]),
                (20, 14), cv2.FONT_HERSHEY_DUPLEX, .4, (0, 0, 0), 1)
            cam_side_img.append(vcat_title_camstate)

        time_per_step = 0.0167
        make_movie(
            cam_plus_img,
            self.folder + '/frames/demo-cam_plus_img{ep:010d}'.format(ep=(self.global_t)),
            duration=len(cam)*time_per_step,
            true_image=True,
            salience=False)
        make_movie(
            cam_side_img,
            self.folder + '/frames/demo-cam_side_img{ep:010d}'.format(ep=(self.global_t)),
            duration=len(state)*time_per_step,
            true_image=True,
            salience=False)
        del cam, state, action, cam_plus_img, cam_side_img

        while max_steps > 0:
            readout_t = self.net.evaluate(self.game_state.s_t)[0]
            action = get_action_index(readout_t, is_random=(random.random() <= 0.05), n_actions=self.game_state.env.action_space.n)

            # take action
            self.game_state.step(action)
            terminal = self.game_state.terminal

            if n_episodes == 0 and self.global_t % 2000000 == 0:
                episode_buffer.append(self.game_state.get_screen_rgb())

            episode_reward += self.game_state.reward
            episode_steps += 1
            max_steps -= 1

            # s_t = s_t1
            self.game_state.update()

            if terminal:
                if get_wrapper_by_name(self.game_state.env, 'EpisodicLifeEnv').was_real_done:
                    if n_episodes == 0 and self.global_t % 2000000 == 0:
                        time_per_step = 0.0167
                        images = np.array(episode_buffer)
                        make_movie(
                            images, self.folder + '/frames/image{ep:010d}'.format(ep=(self.global_t)),
                            duration=len(images)*time_per_step,
                            true_image=True, salience=False)
                        episode_buffer = []
                    n_episodes += 1
                    score_str = colored("score={}".format(episode_reward), "magenta")
                    steps_str = colored("steps={}".format(episode_steps), "blue")
                    log_data = (self.global_t, n_episodes, score_str, steps_str, total_steps)
                    logger.debug("test: global_t={} trial={} {} {} total_steps={}".format(*log_data))
                    total_reward += episode_reward
                    total_steps += episode_steps
                    episode_reward = 0
                    episode_steps = 0
                self.game_state.reset(hard_reset=False)

        if n_episodes == 0:
            total_reward = episode_reward
            total_steps = episode_steps
        else:
            # (timestep, total sum of rewards, total # of steps before terminating)
            total_reward = total_reward / n_episodes
            total_steps = total_steps // n_episodes

        log_data = (self.global_t, total_reward, total_steps, n_episodes)
        logger.debug("test: global_t={} final score={} final steps={} # episodes={}".format(*log_data))
        self.net.record_summary(
            score=total_reward, steps=total_steps,
            episodes=n_episodes, global_t=self.global_t, mode='Test')

        self.rewards['eval'][self.global_t] = (total_reward, total_steps, n_episodes)
        return total_reward, total_steps, n_episodes

    def train_with_demo_memory_only(self):
        assert self.load_demo_memory
        logger.info((colored('Training with demo memory only for {} steps...'.format(self.train_with_demo_steps), 'blue')))
        start_update_counter = self.net.update_counter
        while self.train_with_demo_steps > 0:
            if self.use_transfer:
                self.net.update_counter = 1 # this ensures target network doesn't update
            s_j_batch, a_batch, r_batch, s_j1_batch, terminals = self.replay_memory.random_batch(self.batch)
            # perform gradient step
            self.net.train(s_j_batch, a_batch, r_batch, s_j1_batch, terminals)
            self.train_with_demo_steps -= 1
            if self.train_with_demo_steps % 10000 == 0:
                logger.info("\t{} train with demo steps left".format(self.train_with_demo_steps))
        self.net.update_counter = start_update_counter
        self.net.update_target_network(slow=self.net.slow)
        logger.info((colored('Training with demo memory only completed!', 'green')))

    def run(self):
        # load if starting from a checkpoint
        wall_t = self._load()

        # get the first state by doing nothing and preprocess the image to 80x80x4
        # only reset when it doesn't evaluate first when it enters loop below
        if self.global_t % self.eval_freq != 0:
            self._reset(hard_reset=True)

        # only executed at the very beginning of training and never again
        if self.global_t == 0 and self.train_with_demo_steps > 0:
            self.train_with_demo_memory_only()

        # load one demo for cam
        if self.load_demo_cam:
            # note, tuple length has to be >=2. pad 0 if len==1
            demo_cam_id = tuple(map(int, self.demo_cam_id.split(",")))
            if len(demo_cam_id) == 1:
                demo_cam_id = (*demo_cam_id, '0')
            demo_cam, _, total_rewards_cam, _ = load_memory(
                name=None,
                demo_memory_folder=self.demo_memory_folder,
                demo_ids=demo_cam_id,
                imgs_normalized=False)

            max_idx, _ = max(total_rewards_cam.items(), key=lambda a: a[1])
            size_max_idx_mem = len(demo_cam[max_idx])
            self.test_cam_si = np.zeros(
                (size_max_idx_mem,
                 demo_cam[max_idx].height,
                 demo_cam[max_idx].width,
                 demo_cam[max_idx].phi_length),
                dtype=np.float32)
            for i in range(size_max_idx_mem):
                s0, _, _, _, _, _, _, _ = demo_cam[max_idx][i]
                self.test_cam_si[i] = np.copy(s0)
            logger.info("loaded demo {} for testing CAM".format(demo_cam_id))

        # set start time
        start_time = time.time() - wall_t

        logger.info("replay memory size={}".format(self.replay_memory.size))
        sub_total_reward = 0.0
        sub_steps = 0

        while self.global_t < self.train_max_steps:
            # Evaluation of policy
            if self.global_t % self.eval_freq == 0:
                terminal = 0
                total_reward, total_steps, n_episodes = self.test()
                # re-initialize game for training
                self._reset(hard_reset=True)
                sub_total_reward = 0.0
                sub_steps = 0
                time.sleep(0.5)

            if self.global_t % self.copy_freq == 0:
                self.net.update_target_network(slow=False)

            # choose an action epsilon greedily
            ## self._update_state_input(observation)
            readout_t = self.net.evaluate(self.game_state.s_t)[0]
            action = get_action_index(
                readout_t,
                is_random=(random.random() <= self.epsilon or self.global_t <= self.observe),
                n_actions=self.game_state.env.action_space.n)

            # scale down epsilon
            if self.epsilon > self.final_epsilon and self.global_t > self.observe:
                self.epsilon -= (self.init_epsilon - self.final_epsilon) / self.explore

            ##### HUMAN ADVICE OVERRIDE ACTION #####
            if self.use_human_advice and self.psi > self.final_epsilon:
                use_advice = False
                # After n exploration steps, decay psi
                if (self.global_t - self.observe) >= self.explore:
                    self.psi *= self.init_psi

                # TODO: Determine if I want advice during observation or only during exploration
                if random.random() > self.final_epsilon:
                    psi_cond = True if self.psi == self.init_psi else (self.psi > random.random())
                    if psi_cond:
                        action_advice = self.human_net.evaluate(self.game_state.s_t)[0]
                        action_human = np.argmax(action_advice)
                        if action_advice[action_human] >= self.confidence:
                            action = action_human
                            use_advice = True
            ##### HUMAN ADVICE OVERRIDE ACTION #####

            # Training
            # run the selected action and observe next state and reward
            self.game_state.step(action)
            terminal = self.game_state.terminal
            terminal_ = terminal or ((self.global_t+1) % self.eval_freq == 0)

            # store the transition in D
            ## self.replay_memory.add_sample(observation, action, reward, (1 if terminal_ else 0))
            self.replay_memory.add(
                self.game_state.x_t1, action,
                self.game_state.reward, terminal_,
                self.game_state.lives,
                fullstate=self.game_state.full_state1)

            # update the old values
            sub_total_reward += self.game_state.reward
            sub_steps += 1
            self.global_t += 1
            self.game_state.update()

            # only train if done observing
            if self.global_t > self.observe and self.global_t % self.update_freq == 0:
                s_j_batch, a_batch, r_batch, terminals, s_j1_batch = self.replay_memory.sample(self.batch, reward_type=self.reward_type)
                # perform gradient step
                self.net.train(s_j_batch, a_batch, r_batch, s_j1_batch, terminals, self.global_t)
                # self.net.add_summary(summary, self.global_t)

            if terminal:
                if get_wrapper_by_name(self.game_state.env, 'EpisodicLifeEnv').was_real_done:
                    self.rewards['train'][self.global_t] = (sub_total_reward, sub_steps)
                    score_str = colored("score={}".format(sub_total_reward), "magenta")
                    steps_str = colored("steps={}".format(sub_steps), "blue")
                    log_data = (self.global_t, score_str, steps_str)
                    logger.debug("train: global_t={} {} {}".format(*log_data))
                    self.net.record_summary(
                        score=sub_total_reward, steps=sub_steps,
                        episodes=None, global_t=self.global_t, mode='Train')
                    sub_total_reward = 0.0
                    sub_steps = 0
                self._reset(hard_reset=False)

            # save progress every SAVE_FREQ iterations
            if self.global_t % self.save_freq == 0:
                wall_t = time.time() - start_time
                logger.info('Total time: {} seconds'.format(wall_t))
                wall_t_fname = self.folder + '/' + 'wall_t.' + str(self.global_t)
                epsilon_fname = self.folder + '/epsilon'

                logger.info('Now saving data. Please wait')
                with open(wall_t_fname, 'w') as f:
                    f.write(str(wall_t))
                with open(epsilon_fname, 'w') as f:
                    f.write(str(self.epsilon))

                self.net.save(self.global_t)

                self.replay_memory.save(name=self.name, folder=self.folder, resize=False)
                pickle.dump(self.rewards, open(self.folder + '/' + self.name.replace('-', '_') + '-dqn-rewards.pkl', 'wb'), pickle.HIGHEST_PROTOCOL)
                logger.info('Data saved!')

            # log information
            state = ""
            if self.global_t-1 < self.observe:
                state = "observe"
            elif self.global_t-1 < self.observe + self.explore:
                state = "explore"
            else:
                state = "train"

            if (self.global_t-1) % 10000 == 0:
                if self.use_human_advice:
                    log_data = (
                        state, self.global_t-1, self.epsilon,
                        self.psi, use_advice, action, np.max(readout_t))
                    logger.debug(
                        "{0:}: global_t={1:} epsilon={2:.4f} psi={3:.4f} \
                        advice={4:} action={5:} q_max={6:.4f}".format(*log_data))
                else:
                    log_data = (
                        state, self.global_t-1, self.epsilon,
                        action, np.max(readout_t))
                    logger.debug(
                        "{0:}: global_t={1:} epsilon={2:.4f} action={3:} "
                        "q_max={4:.4f}".format(*log_data))

def playGame():
    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.333)
    with tf.Session(config=tf.ConfigProto(gpu_options=gpu_options, allow_soft_placement=True, log_device_placement=False)) as sess:
        with tf.device('/gpu:'+os.environ["CUDA_VISIBLE_DEVICES"]):
            train(sess)

def main():
    playGame()

if __name__ == "__main__":
    main()
