import os
import time
import warnings

import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import cross_val_score

import mlflow

from joblib import Parallel, delayed

import conf 
from metrics.sra import sra
from metrics.wasserstein import wasserstein_randomization
from metrics.pmse import pmse_ratio

class dumb_predictor():
    """
    Dummy classifier to be used if any of conf.KNOWN_MODELS break.
    Returns single class as prediction.
    """
    def __init__(self, label):
        self.label = label
        
    def predict(self, instances):
        return np.full(len(instances), self.label)

def wasserstein_test(args):
    """
    Parallelizable
    """
    d1, d2, iterations, mlflow_step, name, epsilon, synth_name, dataset_name = args
    wass = wasserstein_randomization(d1, d2, iterations)
    mlflow.set_tags(
        {'wasserstein': str(name),
        # 'mlflow.runName': run_name
        'dataset': dataset_name
        }
    )
    mlflow.log_param('wasserstein_epsilon', str(epsilon))
    mlflow.log_param('wasserstein_synthesizer', str(synth_name))
    mlflow.log_metrics({
        'wasserstein_score': wass,
        })
    return wass

def run_wasserstein(data_dicts, iterations, run_name):
    wass_runs = []
    for d in data_dicts:
        for synth, _ in conf.SYNTHESIZERS:
            for i, e in enumerate(data_dicts[d][synth]):
                arg = data_dicts[d][synth][e], data_dicts[d]['data'], iterations, i, str(d), e, synth, d
                wass_runs.append(arg)

    start = time.time()
    job_num = len(wass_runs)
    results = Parallel(n_jobs=job_num, verbose=1, backend="loky")(
        map(delayed(wasserstein_test), wass_runs))
    end = time.time() - start
    print('Metrics of Wasserstein randomization finished in ' + str(end))
    return results

def pMSE_test(args):
    """
    Parallelizable
    """
    d1, d2, mlflow_step, name, epsilon, synth_name, dataset_name = args
    pmse = pmse_ratio(d1, d2)
    mlflow.set_tags(
        {'pmse': str(name),
        # 'mlflow.runName': run_name
        'dataset': dataset_name
        }
    )
    mlflow.log_param('pmse_epsilon', str(epsilon))
    mlflow.log_param('pmse_synthesizer', str(synth_name))
    mlflow.log_metrics({
        'pmse_score': pmse,
        })
    return pmse

def run_pMSE(data_dicts, run_name):
    pmse_runs = []
    for d in data_dicts:
        for synth, _ in conf.SYNTHESIZERS:
            for i, e in enumerate(data_dicts[d][synth]):
                arg = data_dicts[d][synth][e], data_dicts[d]['data'], i, str(d), e, synth, d
                pmse_runs.append(arg)

    start = time.time()
    job_num = len(pmse_runs)
    results = Parallel(n_jobs=job_num, verbose=1, backend="loky")(
        map(delayed(pMSE_test), pmse_runs))
    end = time.time() - start
    print('Metrics of pMSE finished in ' + str(end))
    return results

def model_auc_roc(args):
    """
    Parallelizable
    """
    model, x_test, y_test, mlflow_step, name = args
    aucroc = -1.0
    if type(model).__name__ != 'dumb_predictor':
        probs = model.predict_proba(x_test)
        unique = np.array(np.unique(y_test))
        
        if len(unique) > 2:
            try:
                aucroc = roc_auc_score(y_test, probs, multi_class='ovr')
            except:
                try:
                    # We can try again, removing classes that have no
                    # examples in yt
                    missing_classes = np.setdiff1d(model.classes_, unique)
                    cols = []
                    for m in missing_classes:
                        ind = np.where(model.classes_==m)
                        cols.append(ind[0].tolist())
                        
                    existant_probs = np.delete(probs, cols, axis=1)

                    for i, row in enumerate(existant_probs):
                        existant_probs[i] = row / np.sum(row)

                    aucroc = roc_auc_score(y_test, existant_probs, multi_class='ovr')
                except:
                    # If this doesnt work, we admit defeat
                    aucroc = 0.0
        else:
            if len(np.unique(y_test)) == 1:
                # Occasionally, the synthesizer will
                # produce synthetic labels of only one class
                # - in this case, aucroc is undefined, so we set 
                # it to 0 (undesirable)
                aucroc = 0.0
            else:
                probs = probs[:,0]
                aucroc = roc_auc_score(y_test, probs)

    mlflow.log_metric(str(name) + '_' + 'aucroc', aucroc, step=mlflow_step)
    return aucroc

def model_accuracy(args):
    """
    Parallelizable
    """
    model, x_test, y_test, mlflow_step, name, synth_name = args
    # predictions = model.predict(x_test)
    # class_report = classification_report(np.ravel(y_test), predictions, labels=np.unique(predictions))
    # print(class_report)
    # mlflow.log_metric(str(name) + '_' + 'class_report', class_report, step=mlflow_step)
    # accuracy = accuracy_score(np.ravel(y_test), predictions)
    # predictions = model.predict(x_test)
    # return accuracy_score(np.ravel(y_test), predictions)
    predictions = model.predict(x_test)
    return accuracy_score(np.ravel(y_test), predictions)
    # if type(model).__name__ != 'dumb_predictor':
    #     scores = cross_val_score(model, x_test, np.ravel(y_test), cv=5)
    #     return scores.mean()
    # else:
        
    # mlflow.set_tags(
    #     {'synthesizer': str(synth_name)}
    # )
    # mlflow.log_metric(str(name), scores.mean(), step=mlflow_step)
    

def fit_a_model(args):
    """
    Parallelizable
    """
    model, model_args, x_train, y_train, x_test, y_test = args
    classifier = model(**model_args)
    
    # NOTE: Not best practice, but otherwise warnings pollute
    try:
        with warnings.catch_warnings():
            classifier.fit(x_train, y_train.values.ravel())
    except:
        # TODO: Suboptimal, but better than everything breaking
        # In the future, we will tag these cases using ml_flow to
        # eliminate them from analysis
        y, counts = np.unique(y_train.values.ravel(), return_counts=True)
        label = y[np.argmax(counts)]
        return (dumb_predictor(label), x_test, y_test)

    return (classifier, x_test, y_test)

def run_model_suite(args):
    """
    Parallelizable
    """
    synthetic_data, target, epsilon_step, test_size, seed, flags, synth_name, epsilon, optional_real_data, rd_flag, dataset_name = args
    models_to_run = []

    X_synth = synthetic_data.loc[:, synthetic_data.columns != target]
    y_synth = synthetic_data.loc[:, synthetic_data.columns == target]
    x_train_synth, x_test_synth, y_train_synth, y_test_synth = train_test_split(X_synth, y_synth, test_size=test_size, random_state=seed)

    if rd_flag:
        X = optional_real_data.loc[:, optional_real_data.columns != target]
        y = optional_real_data.loc[:, optional_real_data.columns == target]
        _, x_test, _, y_test = train_test_split(X, y, test_size=test_size, random_state=seed)
        x_test_synth = x_test
        y_test_synth = y_test

    for model in conf.KNOWN_MODELS:
        m_name = type(model()).__name__
        model_args = conf.MODEL_ARGS[m_name]
        fit_model = (model, model_args, x_train_synth, y_train_synth, x_test_synth, y_test_synth)
        models_to_run.append(fit_model)
    
    start = time.time()
    job_num = len(models_to_run)
    fitted_models = Parallel(n_jobs=job_num, verbose=1, backend="loky")(
        map(delayed(fit_a_model), models_to_run))
    end = time.time() - start
    print('Fitting models finished in ' + str(end))

    predictors = []
    for model in fitted_models:
        classifier, x_t, y_t = model
        m_name = type(classifier).__name__
        pred = (classifier, x_t, y_t, epsilon_step, m_name, synth_name)
        predictors.append(pred)
    
    start = time.time()
    job_num = len(predictors)
    accuracies = Parallel(n_jobs=job_num, verbose=1, backend="loky")(
        map(delayed(model_accuracy), predictors))
    end = time.time() - start
    print('Evaluating models finished in ' + str(end))

    if 'aucroc' in flags:
        start = time.time()
        job_num = len(predictors)
        aucrocs = Parallel(n_jobs=job_num, verbose=1, backend="loky")(
            map(delayed(model_auc_roc), predictors))
        end = time.time() - start
        print('AUCROC finished in ' + str(end))

    index_max = np.argmax(np.array(accuracies))
    mlflow.set_tags(
        {'max_accuracy': conf.KNOWN_MODELS_STR[index_max],
        # 'mlflow.runName': run_name
        'dataset': dataset_name
        }
    )
    mlflow.log_param('synthesizer', str(synth_name))
    mlflow.log_param('epsilon', str(epsilon))
    mlflow.log_metrics({
        'max_accuracy': np.array(accuracies)[index_max]})
        #, 
        #step=epsilon_step)

    return (synth_name + '_' + str(epsilon), accuracies)


def run_ml_eval(data_dict, epsilons, run_name, seed=42, test_size=0.25):
    evals = {}
    
    for d in data_dict:
        real = data_dict[d]["data"]
        target = data_dict[d]['target']
        model_suite_real_args = (real, target, 0, test_size, seed, [], 'real', 0.0, None, False, run_name)
        real_accuracies = run_model_suite(model_suite_real_args)

        synthetic_runs = []
        for n, _ in conf.SYNTHESIZERS:
            for i, e in enumerate(epsilons):
                run_args = (data_dict[d][n][str(e)], target, i, test_size, seed, [], n, e, real, True, run_name)
                synthetic_runs.append(run_args)
        
        start = time.time()
        job_num = len(synthetic_runs)
        synthetic_accuracies = Parallel(n_jobs=job_num, verbose=1, backend="loky")(
            map(delayed(run_model_suite), synthetic_runs))
        end = time.time() - start
        print('ML evaluation suite finished in ' + str(end))
        evals[d] = [real_accuracies] + synthetic_accuracies
    
    return evals