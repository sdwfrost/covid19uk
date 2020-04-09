"Plot functions for Covid-19 data brick"

import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
tfs = tfp.stats


def plot_prediction(prediction_period, sims, case_reports):

    sims = tf.reduce_sum(sims, axis=-2)  # Sum over all meta-populations

    quantiles = [2.5, 50, 97.5]

    dates = np.arange(prediction_period[0],
                      prediction_period[1],
                      np.timedelta64(1, 'D'))
    total_infected = tfs.percentile(tf.reduce_sum(sims[:, :, 1:3], axis=2), q=quantiles, axis=0)
    removed = tfs.percentile(sims[:, :, 3], q=quantiles, axis=0)
    removed_observed = tfs.percentile(removed * 0.1, q=quantiles, axis=0)

    fig = plt.figure()
    ax = fig.gca()
    filler = ax.fill_between(dates, total_infected[0, :], total_infected[2, :], color='lightgray', alpha=0.8, label="95% credible interval")
    ax.fill_between(dates, removed[0, :], removed[2, :], color='lightgray', alpha=0.8)
    ax.fill_between(dates, removed_observed[0, :], removed_observed[2, :], color='lightgray', alpha=0.8)
    ti_line = ax.plot(dates, total_infected[1, :], '-', color='red', alpha=0.4, label="Infected")
    rem_line = ax.plot(dates, removed[1, :], '-', color='blue', label="Removed")
    ro_line = ax.plot(dates, removed_observed[1, :], '-', color='orange', label='Predicted detections')

    data_range = [case_reports.index.min(), case_reports.index.max()]
    one_day = np.timedelta64(1, 'D')
    data_dates = np.arange(data_range[0], data_range[1]+one_day, one_day)
    marks = ax.plot(data_dates, np.cumsum(case_reports), '+', label='Observed cases')
    plt.legend([ti_line[0], rem_line[0], ro_line[0], filler, marks[0]],
               ["Infected", "Removed", "Predicted detections", "95% credible interval", "Observed counts"])
    ax.grid(color='lightgray', linestyle='dotted')
    ax.set_xlabel("Date")
    ax.set_ylabel("Individuals")
    fig.autofmt_xdate()
    return fig, ax


def plot_case_incidence(date_range, sims):

    # Number of new cases per day
    dates = np.arange(date_range[0], date_range[1])
    new_cases = sims[:, :, :, 3].sum(axis=2)
    new_cases = new_cases[:, 1:] - new_cases[:, :-1]

    new_cases = tfs.percentile(new_cases,  q=[2.5, 50, 97.5], axis=0)/10000.
    fig = plt.figure()
    ax = fig.gca()
    ax.fill_between(dates[:-1], new_cases[0, :], new_cases[2, :], color='lightgray', label="95% credible interval")
    ax.plot(dates[:-1], new_cases[1, :], '-', alpha=0.2, label='New cases')
    ax.grid(color='lightgray', linestyle='dotted')
    ax.set_xlabel("Date")
    ax.set_ylabel("Incidence per 10,000")
    fig.autofmt_xdate()
    return fig, ax