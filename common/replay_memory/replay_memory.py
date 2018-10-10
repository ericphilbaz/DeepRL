#!/usr/bin/env python3
"""
Copyright (c) 2014, Nathan Sprague
All rights reserved.

Original code: https://goo.gl/dp2qRV

This class stores all of the samples for training.  It is able to
construct randomly selected batches of phi's from the stored history.
"""

import numpy as np
import time
import logging

from common.util import save_compressed_images

try:
    import cPickle as pickle
except ImportError:
    import pickle

logger = logging.getLogger("replay_memory")

class ReplayMemory(object):
    """
    This replay memory assumes it's a single episode of memory in sequence
    """
    def __init__(self,
        width=1, height=1, rng=np.random.RandomState(),
        max_steps=10, phi_length=4, num_actions=1, wrap_memory=False,
        full_state_size=1013, clip_reward=True):
        """Construct a replay memory.

        Arguments:
            width, height - image size
            max_steps - the number of time steps to store
            phi_length - number of images to concatenate into a state
            rng - initialized numpy random number generator, used to
            choose random minibatches

        """
        # Store arguments.
        self.width = width
        self.height = height
        self.max_steps = max_steps
        self.phi_length = phi_length
        self.num_actions = num_actions
        self.rng = rng
        self.full_state_size = full_state_size
        self.clip_reward = clip_reward

        # Allocate the circular buffers and indices.
        self.imgs = np.zeros((self.max_steps, height, width), dtype=np.uint8)
        self.actions = np.zeros(self.max_steps, dtype=np.uint8)
        self.rewards = np.zeros(self.max_steps, dtype=np.float32)
        self.terminal = np.zeros(self.max_steps, dtype=np.uint8)
        self.lives = np.zeros(self.max_steps, dtype=np.int32)
        self.loss_life = np.zeros(self.max_steps, dtype=np.uint8)
        self.gain_life = np.zeros(self.max_steps, dtype=np.uint8)
        self.full_state = np.zeros((self.max_steps, full_state_size), dtype=np.uint8)

        self.size = 0
        self.imgs_normalized = False

        self.wrap_memory = wrap_memory
        self.bottom = 0
        self.top = 0

    def normalize_images(self):
        if not self.imgs_normalized:
            logger.info("Normalizing images...")
            temp = self.imgs
            self.imgs = temp.astype(np.float32) / 255.0
            del temp
            self.imgs_normalized = True
            logger.info("Images normalized")

    def propagate_rewards(self, gamma=0.95, clip=False, normalize=False, minmax_scale=False, exclude_outlier=False, max_reward=0):
        logger.info("Propagating rewards...")
        logger.info("    reward size: {}".format(np.shape(self.rewards)[0]))
        logger.info("    gamma: {}".format(gamma))
        logger.info("    clip: {}".format(clip))
        logger.info("    normalize: {}".format(normalize))
        logger.info("    minmax_scale: {}".format(minmax_scale))

        logger.debug("    mean: {}".format(np.mean(np.abs(self.rewards))))
        logger.debug("    median: {}".format(np.median(np.abs(self.rewards))))

        if clip:
            np.clip(self.rewards, -1., 1., out=self.rewards)
        elif exclude_outlier and max_reward != 0:
            rewards = self.rewards[np.nonzero(self.rewards)]
            outliers = self.rewards[np.abs(self.rewards - np.mean(rewards)) > 2*np.std(rewards)]
            logger.debug("    outliers: {}".format(outliers))
            for outlier in outliers:
                if outlier != 0:
                    self.rewards[self.rewards == outlier] = max_reward if outlier > 0 else -max_reward
        if normalize and max_reward != 0:
            logger.debug("    max_reward: {}".format(max_reward))
            self.rewards = self.rewards / max_reward

        for i in range(self.size-2, 0, -1):
            #if self.rewards[i] != 0:
            self.rewards[i] = self.rewards[i] + gamma*self.rewards[i+1]

        if minmax_scale:
            from sklearn.preprocessing import MinMaxScaler
            rewards = self.rewards.reshape(-1, 1)
            scaler.fit(rewards)
            rewards = scaler.transform(rewards)
            self.rewards = rewards.reshape(-1)

        logger.debug("    max_reward: {}".format(np.linalg.norm(self.rewards, np.inf)))
        logger.debug("    min_reward: {}".format(np.min(np.abs(self.rewards[np.nonzero(self.rewards)]))))
        logger.info("Rewards propagated!")

    def resize(self):
        logger.info("Resizing replay memory...")
        logger.debug("Current specs: size={} max_steps={}".format(self.size, self.max_steps))
        logger.debug("    images shape: {}".format(np.shape(self.imgs)))
        logger.debug("    actions shape: {}".format(np.shape(self.actions)))
        logger.debug("    rewards shape: {}".format(np.shape(self.rewards)))
        logger.debug("    terminal shape: {}".format(np.shape(self.terminal)))
        logger.debug("    lives shape: {}".format(np.shape(self.lives)))
        logger.debug("    loss_life shape: {}".format(np.shape(self.loss_life)))
        logger.debug("    gain_life shape: {}".format(np.shape(self.gain_life)))
        logger.debug("    full_state shape: {}".format(np.shape(self.full_state)))
        tmp_imgs = np.delete(self.imgs, range(self.size,self.max_steps), axis=0)
        tmp_actions = np.delete(self.actions, range(self.size, self.max_steps), axis=0)
        tmp_rewards = np.delete(self.rewards, range(self.size, self.max_steps), axis=0)
        tmp_terminal = np.delete(self.terminal, range(self.size, self.max_steps), axis=0)
        tmp_lives = np.delete(self.lives, range(self.size, self.max_steps), axis=0)
        tmp_losslife = np.delete(self.loss_life, range(self.size, self.max_steps), axis=0)
        tmp_gainlife = np.delete(self.gain_life, range(self.size, self.max_steps), axis=0)
        tmp_fullstate = np.delete(self.full_state, range(self.size, self.max_steps), axis=0)
        del self.imgs, self.actions, self.rewards, self.terminal, \
            self.lives, self.loss_life, self.gain_life, self.full_state
        self.imgs = tmp_imgs
        self.actions = tmp_actions
        self.rewards = tmp_rewards
        self.terminal = tmp_terminal
        self.lives = tmp_lives
        self.loss_life = tmp_losslife
        self.gain_life = tmp_gainlife
        self.full_state = tmp_fullstate
        self.max_steps = self.size
        logger.info("Resizing completed!")
        logger.debug("Updated specs: size={} max_steps={}".format(self.size, self.max_steps))
        logger.debug("    images shape: {}".format(np.shape(self.imgs)))
        logger.debug("    actions shape: {}".format(np.shape(self.actions)))
        logger.debug("    rewards shape: {}".format(np.shape(self.rewards)))
        logger.debug("    terminal shape: {}".format(np.shape(self.terminal)))
        logger.debug("    lives shape: {}".format(np.shape(self.lives)))
        logger.debug("    loss_life shape: {}".format(np.shape(self.loss_life)))
        logger.debug("    gain_life shape: {}".format(np.shape(self.gain_life)))
        logger.debug("    full_state shape: {}".format(np.shape(self.full_state)))

    def add(self, img, action, reward, terminal, lives, losslife=False, gainlife=False, fullstate=np.zeros(1021)):
        """Add a time step record.

        Arguments:
            img -- observed image
            action -- action chosen by the agent
            reward -- reward received after taking the action
            terminal -- boolean indicating whether the episode ended
            after this time step
        """
        if not self.wrap_memory and self.size == self.max_steps:
            logger.warn("Memory is full. Data not added!")
            return

        if self.wrap_memory:
            idx = self.top
        else:
            idx = self.size

        self.imgs[idx] = img
        self.actions[idx] = action
        self.rewards[idx] = reward
        self.terminal[idx] = terminal
        self.lives[idx] = lives
        self.loss_life[idx] = losslife
        self.gain_life[idx] = gainlife
        self.full_state[idx] = fullstate

        if self.wrap_memory and self.size == self.max_steps:
            self.bottom = (self.bottom + 1) % self.max_steps
        else:
            self.size += 1

        if self.wrap_memory:
            self.top = (self.top + 1) % self.max_steps

    def __len__(self):
        """Return an approximate count of stored state transitions."""
        # TODO: Properly account for indices which can't be used, as in
        # random_batch's check.
        return max(0, self.size - self.phi_length)

    def __getitem__(self, key):
        indices = np.arange(key, key + self.phi_length)
        indices_next = np.arange(1 + key, 1 + key + self.phi_length)
        end_index = key + self.phi_length - 1
        state = np.zeros(
            (self.height, self.width, self.phi_length),
            dtype=np.float32 if self.imgs_normalized else np.uint8)
        next_state = np.zeros(
            (self.height, self.width, self.phi_length),
            dtype=np.float32 if self.imgs_normalized else np.uint8)

        if self.wrap_memory:
            mode = 'wrap'
            if np.any(self.terminal.take(indices[:-1], mode='wrap')):
                return None, None, None, None, None, None
        else:
            mode = 'raise'
            if end_index >= self.size or np.any(self.terminal.take(indices[:-1], axis=0)):
                return None, None, None, None, None, None

        temp = self.imgs.take(indices, axis=0, mode=mode)
        for i in range(self.phi_length):
            state[:, :, i] = temp[i]

        action = self.actions.take(end_index, axis=0, mode=mode)
        reward = self.rewards.take(end_index, mode=mode)
        if self.clip_reward:
            reward = np.sign(reward)
        terminal = self.terminal.take(end_index, mode=mode)
        lives = self.lives.take(end_index, mode=mode)
        losslife = self.loss_life.take(end_index, mode=mode)
        gainlife = self.gain_life.take(end_index, mode=mode)

        if not terminal:
            temp = self.imgs.take(indices_next, axis=0, mode=mode)
            for i in range(self.phi_length):
                next_state[:, :, i] = temp[i]

        return state, action, reward, terminal, next_state, lives, losslife, gainlife

    def __str__(self):
        specs = "Replay memory:\n"
        specs += "  size:{}\n".format(self.size)
        specs += "  max_steps:{}\n".format(self.max_steps)
        specs += "  imgs shape:{}\n".format(np.shape(self.imgs))
        return specs

    def get_item(self, key):
        return self.__getitem__(key)

    def sample_sequential(self, batch_size):
        """Return corresponding states, actions, rewards, terminal status, and
        next_states for batch_size randomly chosen state transitions.
        """
        assert not self.wrap_memory

        # Allocate the response.
        states = np.zeros(
            (batch_size, 84, 84, self.phi_length),
            dtype=np.float32 if self.imgs_normalized else np.uint8)
        actions = np.zeros((batch_size, self.num_actions), dtype=np.float32)
        rewards = np.zeros(batch_size, dtype=np.float32)
        terminals = np.zeros(batch_size, dtype=np.int)
        # lives = np.zeros(batch_size, dtype=np.int)
        # losslifes = np.zeros(batch_size, dtype=np.int)
        # gainlifes = np.zeros(batch_size, dtype=np.int)

        # Randomly choose a time step from the replay memory
        # within requested batch_size
        # randint low (inclusive) to high (exclusive)
        high = (self.size + 1) - (self.phi_length - 1 + batch_size)
        assert high > 0 # crash if not enough memory

        # ensure no terminal besides the last index
        while True:
            self.random_index = self.rng.randint(0, high)
            indices = np.arange(self.random_index, self.random_index + batch_size + (self.phi_length - 1))
            if not np.any(self.terminal.take(indices[:-1], axis=0)):
                index = self.random_index
                break
        #end_index = (index + self.phi_length-1) + (batch_size - 1)

        for count in range(batch_size):
            s0, a, r, t, s1, l, ll, gl = self[index]
            # Add the state transition to the response.
            states[count] = np.copy(s0)
            actions[count][a] = 1. # convert to one-hot vector
            rewards[count] = r
            terminals[count] = t
            # lives[count] = l
            # losslifes[count] = ll
            # gainlifes[count] = gl
            index += 1

        return states, actions, rewards, terminals #, lives, losslifes, gainlifes

    def sample2(self, batch_size, normalize=False, k_bad_states=0, onevsall=False, n_class=None):
        """Return corresponding states, actions, rewards, terminal status, and
        next_states for batch_size randomly chosen state transitions.
        """
        assert not self.wrap_memory
        # Allocate the response.
        states = np.zeros(
            (batch_size, self.height, self.width, self.phi_length),
            dtype=np.float32 if (normalize or self.imgs_normalized) else np.uint8)
        if onevsall:
            actions = np.zeros((batch_size, 2), dtype=np.float32)
        else:
            actions = np.zeros((batch_size, self.num_actions), dtype=np.float32)
        rewards = np.zeros(batch_size, dtype=np.float32)
        terminals = np.zeros(batch_size, dtype=np.int)
        # lives = np.zeros(batch_size, dtype=np.int)
        # losslifes = np.zeros(batch_size, dtype=np.int)
        # gainlifes = np.zeros(batch_size, dtype=np.int)

        # Randomly choose a time step from the replay memory
        # within requested batch_size
        # randint low (inclusive) to high (exclusive)
        high = (self.size + 1) - self.phi_length
        assert high > 0 # crash if not enough memory

        count = 0
        while count < batch_size:
            index = self.rng.randint(0, high)
            if k_bad_states:
                # do not train k steps to a bad state (negative reward or loss life)
                st_idx = index + (self.phi_length-1)
                en_idx = index + (self.phi_length-1) + k_bad_states
                if (np.any(self.rewards[st_idx:en_idx] < 0) or \
                    np.any(self.loss_life[st_idx:en_idx] == 1)):
                    continue

            s0, a, r, t, s1, l, ll, gl = self[index]
            if s is None or s1 is None:
                continue
            # Add the state transition to the response.
            states[count] = np.copy(s0)
            if normalize and not self.imgs_normalized:
                states[count] /= 255.0
            if onevsall:
                if a == n_class:
                    actions[count][0] = 1
                else:
                    actions[count][1] = 1
            else:
                actions[count][a] = 1 # convert to one-hot vector
            rewards[count] = r
            terminals[count] = t
            # lives[count] = l
            # losslifes[count] = ll
            # gainlifes[count] = gl
            count += 1

        return states, actions, rewards, terminals #, lives, losslifes, gainlifes

    def sample(self, batch_size, normalize=False, onevsall=False, n_class=None):
        """Return corresponding states, actions, rewards, terminal status, and
        next_states for batch_size randomly chosen state transitions.
        """
        assert self.wrap_memory
        # Allocate the response.
        states = np.zeros(
            (batch_size, self.height, self.width, self.phi_length),
            dtype=np.float32 if (normalize or self.imgs_normalized) else np.uint8)
        next_states = np.zeros(
            (batch_size, self.height, self.width, self.phi_length),
            dtype=np.float32 if (normalize or self.imgs_normalized) else np.uint8)
        if onevsall:
            actions = np.zeros((batch_size, 2), dtype=np.float32)
        else:
            actions = np.zeros((batch_size, self.num_actions), dtype=np.float32)
        rewards = np.zeros(batch_size, dtype=np.float32)
        terminals = np.zeros(batch_size, dtype=np.int)
        # lives = np.zeros(batch_size, dtype=np.int)
        # losslifes = np.zeros(batch_size, dtype=np.int)
        # gainlifes = np.zeros(batch_size, dtype=np.int)

        count = 0
        while count < batch_size:
            index = self.rng.randint(self.bottom, self.bottom + self.size - self.phi_length)
            indices = np.arange(index, index + self.phi_length + 1)
            end_index = index + self.phi_length - 1

            if np.any(self.terminal.take(indices[:-1], mode='wrap')):
                continue

            s0, a, r, t, s1, l, ll, gl = self[index]
            if s0 is None or s1 is None:
                continue
            # Add the state transition to the response.
            states[count] = np.copy(s0)
            next_states[count] = np.copy(s1)
            if normalize and not self.imgs_normalized:
                states[count] /= 255.0
                next_states[count] /= 255.0
            if onevsall:
                if a == n_class:
                    actions[count][0] = 1
                else:
                    actions[count][1] = 1
            else:
                actions[count][a] = 1 # convert to one-hot vector
            rewards[count] = r
            terminals[count] = t
            # lives[count] = l
            # losslifes[count] = ll
            # gainlifes[count] = gl
            count += 1

        return states, actions, rewards, terminals, next_states #, lives, losslifes, gainlifes

    def save(self, name=None, folder=None, resize=False):
        assert name is not None
        assert folder is not None

        if resize:
            # Resize replay memory to exact memory size
            self.resize()
        data = {'width':self.width,
                'height':self.height,
                'max_steps':self.max_steps,
                'phi_length':self.phi_length,
                'num_actions':self.num_actions,
                'actions':self.actions,
                'rewards':self.rewards,
                'terminal':self.terminal,
                'lives':self.lives,
                'loss_life':self.loss_life,
                'gain_life':self.gain_life,
                'full_state_size': self.full_state_size,
                'full_state': self.full_state,
                'size':self.size,
                'wrap_memory':self.wrap_memory,
                'top':self.top,
                'bottom':self.bottom,
                'imgs_normalized':self.imgs_normalized}
        images = self.imgs
        pkl_file = '{}.pkl'.format(name)
        h5_file = '{}-images.h5'.format(name)
        pickle.dump(data, open(folder + '/' + pkl_file, 'wb'), pickle.HIGHEST_PROTOCOL)
        logger.info('Compressing and saving replay memory...')
        save_compressed_images(folder + '/' + h5_file, images)
        logger.info('Compressed and saved replay memory')

    def load(self, name=None, folder=None):
        assert name is not None
        assert folder is not None

        from common.util import get_compressed_images
        try:
            import cPickle as pickle
        except ImportError:
            import pickle

        pkl_file = '{}.pkl'.format(name)
        h5_file = '{}-images.h5'.format(name)
        data = pickle.load(open(folder + '/' + pkl_file, 'rb'))
        self.width = data['width']
        self.height = data['height']
        self.max_steps = data['max_steps']
        self.phi_length = data['phi_length']
        self.num_actions = data['num_actions']
        self.actions = data['actions']
        self.rewards = data['rewards']
        self.terminal = data['terminal']
        self.lives = data.get('lives', np.zeros(self.max_steps, dtype=np.int32))
        self.loss_life = data.get('loss_life', np.zeros(self.max_steps, dtype=np.uint8))
        self.gain_life = data.get('gain_life', np.zeros(self.max_steps, dtype=np.uint8))
        self.full_state_size = data.get('full_state_size', self.full_state_size)
        self.full_state = data.get('full_state', np.zeros((self.max_steps, self.full_state_size), dtype=np.uint8))
        self.size = data['size']
        self.wrap_memory = data['wrap_memory']
        self.top = data['top']
        self.bottom = data['bottom']
        self.imgs_normalized = data['imgs_normalized']
        self.imgs = get_compressed_images(folder + '/' + h5_file + '.gz')


def test_1(env_id):
    from common.util import get_compressed_images
    try:
        import cPickle as pickle
    except ImportError:
        import pickle

    folder = env_id.replace('-', '_') + "_test_demo_samples"
    rm = ReplayMemory()
    rm.load(name=env_id, folder=(folder + '/001'))
    print (rm)

    for i in range(1000):
        states, actions, rewards, terminals, lives, losslife, gainlife = rm.random_batch(20)

    import cv2
    count = 0
    state, _, _, _, _, _, _ = rm[count]
    print ("shape:", np.shape(state))
    while count < len(rm):
        state, _, _, _, _ = rm[count]
        cv2.imshow(env_id, state)
        # cv2.imshow("one", state[:,:,0])
        # cv2.imshow("two", state[:,:,1])
        # cv2.imshow("three", state[:,:,2])
        # cv2.imshow("four", state[:,:,3])
        diff1 = cv2.absdiff(state[:,:,3], state[:,:,2])
        diff2 = cv2.absdiff(state[:,:,3], state[:,:,1])
        diff3 = cv2.absdiff(state[:,:,3], state[:,:,0])
        diff = cv2.addWeighted(diff1, 0.8, diff2, 0.2, 0.0)
        diff = cv2.addWeighted(diff, 0.8, diff3, 0.2, 0.0)
        cv2.imshow("difference", diff)
        cv2.waitKey(20)
        count += 1
    print ("total transitions:", len(rm))
    print ("size:", rm.size)

def test_2(env_id):
    from common.util import get_compressed_images
    try:
        import cPickle as pickle
    except ImportError:
        import pickle

    folder = "demo_samples/{}".format(env_id.replace('-', '_'))
    rm = ReplayMemory()
    rm.load(name=env_id, folder=(folder + '/001'))
    print (rm)

    state, a, r, t, l, ll, gl = rm[0]
    print (state)
    print (a, r, t, l, ll, gl)

    rm.normalize_images()
    state, a, r, t, l, ll, gl = rm[2]
    print (state)
    print (a, r, t, l, ll, gl)

    for count in range(100):
        _, a, r, t, l, ll, gl = rm[count]
        print (a,r,t,l,ll,gl)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('env', type=str)
    args = parser.parse_args()

    test_2(args.env)