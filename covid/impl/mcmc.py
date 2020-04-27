"""MCMC Update classes for stochastic epidemic models"""
import tensorflow as tf
import tensorflow_probability as tfp
from tensorflow_probability.python.util import SeedStream

tfd = tfp.distributions

DTYPE = tf.float64


class DeterministicFloatX(tfd.Deterministic):
    def _prob(self, x):
        loc = tf.convert_to_tensor(self.loc)
        return tf.reduce_sum(tf.cast(tf.abs(x - loc) <= self._slack(loc), dtype=DTYPE))


def matrix_where(condition):
    nrow, ncol = condition.shape
    msk = tf.reshape(condition, [-1])
    msk_idx = tf.boolean_mask(tf.range(tf.size(msk), dtype=tf.int64), msk)
    true_coords = tf.stack([msk_idx // ncol, msk_idx % ncol],
                            axis=-1)
    return true_coords

def make_event_time_move(counts_matrix, q, p, alpha):
    """Returns a proposal to move infection times.

    Algorithm:
        1. Choose random batch of coordinate [day, class] in event_matrix to update;
        2. Choose a number of items to move using Binomial(n_{tm}, p)
        3. Choose a new timepoint (dim 0 in events) to move to by Uniform([-1,1]) * Poisson(alpha)

    :param counts_matrix: matrix of number of units per day (dim 0) per class (dim 1)
    :param p: probability that a unit within a day/class gets chosen to move
    :param alpha: the magnitude of the distance to move the chosen units in time.
    :returns: an instance of tfd.JointDistributionNamed over all random numbers above.
    """
    counts_matrix = tf.convert_to_tensor(counts_matrix, dtype=DTYPE)
    p = tf.convert_to_tensor(p, dtype=DTYPE)
    alpha = tf.convert_to_tensor(alpha, dtype=DTYPE)
    q = tf.convert_to_tensor(q, dtype=DTYPE)

    nz_idx = matrix_where(counts_matrix > 0)

    # Choose which elements to move
    ix = tfd.Sample(tfd.Bernoulli(probs=q, dtype=tf.bool), [tf.shape(nz_idx)[0]], name='ix')

    def tm(ix):
        # Return coordinates of elements to move
        return DeterministicFloatX(tf.boolean_mask(nz_idx, ix, axis=0), name='coords')

    def n_events(tm):
        # Choose number of units at each coordinate to move
        n = tf.gather_nd(counts_matrix, indices=tm)
        return tfd.Binomial(n, probs=p, name='n_events')

    def dir(tm):
        # Sample direction to move in
        return tfd.Sample(tfd.Bernoulli(probs=tf.constant(0.5, dtype=DTYPE)), [tf.shape(tm)[0]], name='dir')

    def d_mag(tm):
        # Sample distance to move each set of units
        return tfd.Sample(tfd.Geometric(probs=alpha, name='d_mag'), [tf.shape(tm)[0]], name='d_mag')

    def distance(dir, d_mag):
        # Compute the distance to move as product of direction and distance
        return DeterministicFloatX(tf.gather(tf.constant([-1., 1.], dtype=DTYPE), dir)*d_mag, name='distance')

    return tfd.JointDistributionNamed({
        'ix': ix,
        'tm': tm,
        'n_events': n_events,
        'dir': dir,
        'd_mag': d_mag,
        'distance': distance})


class UncalibratedEventTimesUpdate(tfp.mcmc.TransitionKernel):
    def __init__(self,
                 target_log_prob_fn,
                 transition_coord,
                 q,
                 p,
                 alpha,
                 seed=None,
                 name=None):
        """An uncalibrated random walk for event times.
        :param target_log_prob_fn: the log density of the target distribution
        :param transition_coord: the coordinate of the transition in the transition matrix
        :param p: the proportion of events to move
        :param alpha: the magnitude of the distance over which to move
        :param seed: a random seed stream
        :param name: the name of the update step
        """
        self._target_log_prob_fn = target_log_prob_fn
        self._seed_stream = SeedStream(seed, salt='UncalibratedEventTimesUpdate')
        self._name = name
        self._parameters = dict(
            target_log_prob_fn=target_log_prob_fn,
            transition_coord=tf.convert_to_tensor(transition_coord, dtype=tf.int64),
            q=q,
            p=p,
            alpha=alpha,
            seed=seed,
            name=name)

    @property
    def target_log_prob_fn(self):
        return self._parameters['target_log_prob_fn']

    @property
    def transition_coord(self):
        return self._parameters['transition_coord']

    @property
    def seed(self):
        return self._parameters['seed']

    @property
    def name(self):
        return self._parameters['name']

    @property
    def parameters(self):
        """Return `dict` of ``__init__`` arguments and their values."""
        return self._parameters

    @property
    def is_calibrated(self):
        return False

    def one_step(self, current_state, previous_kernel_results):
        with tf.name_scope('uncalibrated_event_times_rw/onestep'):
            proposal = make_event_time_move(current_state[..., self.transition_coord[0],
                                                          self.transition_coord[1]],
                                            self._parameters['q'],
                                            self._parameters['p'],
                                            self._parameters['alpha'])
            x_star = proposal.sample(seed=self.seed)  # This is the move to make
            n_move = tf.shape(x_star['tm'],out_type=x_star['tm'].dtype)[0]  # Number of time/metapop moves

            coord_dtype = x_star['tm'].dtype
            state_coord = tf.broadcast_to(self.transition_coord,
                                         [n_move, self.transition_coord.shape[0]])

            # Calculate the coordinate that we'll move events to
            indices = tf.stack([tf.range(n_move, dtype=coord_dtype),
                                tf.zeros(n_move, dtype=coord_dtype)], axis=-1)
            coord_to_move_to = tf.tensor_scatter_nd_add(tensor=x_star['tm'],
                                                        indices=indices,
                                                        updates=tf.cast(x_star['distance'], tf.int64))

            # Update the state
            indices = [tf.concat([x_star['tm'], state_coord], axis=-1),
                       tf.concat([coord_to_move_to, state_coord], axis=-1)]
            next_state = tf.tensor_scatter_nd_add(tensor=current_state,
                                                  indices=indices,
                                                  updates=[-x_star['n_events'], x_star['n_events']])  # Update state based on move

            next_target_log_prob = self.target_log_prob_fn(next_state)

            reverse_n = tf.gather_nd(next_state, indices[1])
            log_acceptance_correction = tfd.Binomial(reverse_n, probs=self._parameters['p']).log_prob(x_star['n_events'])
            log_acceptance_correction -= proposal.log_prob(x_star)  # move old->new
            log_acceptance_correction = tf.reduce_sum(log_acceptance_correction)

            return [next_state,
                    tfp.mcmc.random_walk_metropolis.UncalibratedRandomWalkResults(
                        log_acceptance_correction=log_acceptance_correction,
                        target_log_prob=next_target_log_prob
                    )]

    def bootstrap_results(self, init_state):
        with tf.name_scope('uncalibrated_event_times_rw/bootstrap_results'):
            init_state=tf.convert_to_tensor(init_state, dtype=DTYPE)
            init_target_log_prob = self.target_log_prob_fn(init_state)
            return tfp.mcmc.random_walk_metropolis.UncalibratedRandomWalkResults(
                log_acceptance_correction=tf.constant(0., dtype=DTYPE),
                target_log_prob=init_target_log_prob
            )


class Gibbs(tfp.mcmc.TransitionKernel):
    def __init__(self, target_log_prob_fn, make_kernel_fns):
        self._target_log_prob_fn = target_log_prob_fn
        self._make_kernel_fns = make_kernel_fns

    def is_calibrated(self):
        return True

    def one_step(self, state, _):
        for i, make_kernel_fn in enumerate(self._make_kernel_fns):
            def _target_log_prob_fn_part(state_part):
                state[i] = state_part
                return self._target_log_prob_fn(*state)

            kernel = make_kernel_fn(_target_log_prob_fn_part)
            state[i], _ = kernel.one_step(state[i], kernel.bootstrap_results(state[i]))
        return state, ()

    def bootstrap_results(self, state):
        return ()