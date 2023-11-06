"""This function will load the given data and continuosly interpet selected patients"""
""" 
Lily Li
Last modified: 2022 01
"""
import argparse
import pickle as pickle
import numpy as np
import pandas as pd
import tensorflow as tf
import keras.backend as K
from keras.models import load_model, Model
from keras.preprocessing import sequence
from keras.constraints import Constraint
from keras.utils.data_utils import Sequence
from tqdm import tqdm
import pdb
import datetime 

def import_model(path):
    """Import model from given path and assign it to appropriate devices"""
    K.clear_session()
    config = tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)
    config.gpu_options.allow_growth = True
    tfsess = tf.Session(config=config)
    K.set_session(tfsess)
    model = load_model(path, custom_objects={'FreezePadding':FreezePadding,
                                             'FreezePadding_Non_Negative':FreezePadding_Non_Negative})
    model_with_attention = Model(model.inputs, model.outputs +\
                                              [model.get_layer(name='softmax_1').output,\
                                               model.get_layer(name='beta_dense_0').output])
    return model, model_with_attention

def get_model_parameters(model):
    """Extract model arguments that were used during training"""
    class ModelParameters:
        """Helper class to store model parametesrs in the same format as ARGS"""
        def __init__(self):
            self.num_codes = None
            self.numeric_size = None
            self.use_time = None
            self.emb_weights = None
            self.output_weights = None
            self.bias = None


    params = ModelParameters()
    names = [layer.name for layer in model.layers]
    params.num_codes = model.get_layer(name='embedding').input_dim-1
    params.emb_weights = model.get_layer(name='embedding').get_weights()[0]
    params.output_weights, params.bias = model.get_layer(name='time_distributed_out').get_weights()
    print('Model bias: {}'.format(params.bias))
    if 'numeric_input' in names:
        params.numeric_size = model.get_layer(name='numeric_input').input_shape[2]
        #Add artificial embeddings for each numeric feature and extend the embedding weights
        #Numeric embeddings is just 1 for 1 dimension of the embedding which corresponds to taking value as is
        numeric_embeddings = np.zeros((params.numeric_size, params.emb_weights.shape[1]+params.numeric_size))
        for i in range(params.numeric_size):
            numeric_embeddings[i, params.emb_weights.shape[1]+i] = 1
        #Extended embedding is original embedding extended to larger output size and numerics embeddings added
        params.emb_weights = np.append(params.emb_weights,
                                       np.zeros((params.num_codes+1, params.numeric_size)),
                                       axis=1)
        params.emb_weights = np.append(params.emb_weights, numeric_embeddings, axis=0)
    else:
        params.numeric_size = 0
    if 'time_input' in names:
        params.use_time = True
    else:
        params.use_time = False
    return params

class FreezePadding_Non_Negative(Constraint):
    """Freezes the last weight to be near 0 and prevents non-negative embeddings"""
    def __call__(self, w):
        other_weights = K.cast(K.greater_equal(w, 0)[:-1], K.floatx())
        last_weight = K.cast(K.equal(K.reshape(w[-1, :], (1, K.shape(w)[1])), 0.), K.floatx())
        appended = K.concatenate([other_weights, last_weight], axis=0)
        w *= appended
        return w

class FreezePadding(Constraint):
    """Freezes the last weight to be near 0."""
    def __call__(self, w):
        other_weights = K.cast(K.ones(K.shape(w))[:-1], K.floatx())
        last_weight = K.cast(K.equal(K.reshape(w[-1, :], (1, K.shape(w)[1])), 0.), K.floatx())
        appended = K.concatenate([other_weights, last_weight], axis=0)
        w *= appended
        return w

class SequenceBuilder(Sequence):
    """Generate Batches of data"""
    def __init__(self, data, model_parameters, ARGS):
        #Receive all appropriate data
        self.codes = data[0]
        index = 1
        if model_parameters.numeric_size:
            self.numeric = data[index]
            index += 1

        if model_parameters.use_time:
            self.time = data[index]

        self.num_codes = model_parameters.num_codes
        self.batch_size = ARGS.batch_size
        self.numeric_size = model_parameters.numeric_size
        self.use_time = model_parameters.use_time

    def __len__(self):
        """Compute number of batches.
        Add extra batch if the data doesn't exactly divide into batches
        """
        if len(self.codes)%self.batch_size == 0:
            return len(self.codes) // self.batch_size
        return len(self.codes) // self.batch_size+1

    def __getitem__(self, idx):
        """Get batch of specific index"""
        def pad_data(data, length_visits, length_codes, pad_value=0):
            """Pad data to desired number of visiits and codes inside each visit"""
            zeros = np.full((len(data), length_visits, length_codes), pad_value)
            for steps, mat in zip(data, zeros):
                if steps != [[-1]]:
                    for step, mhot in zip(steps, mat[-len(steps):]):
                        #Populate the data into the appropriate visit
                        mhot[:len(step)] = step

            return zeros
        #Compute reusable batch slice
        batch_slice = slice(idx*self.batch_size, (idx+1)*self.batch_size)
        x_codes = self.codes[batch_slice]
        #Max number of visits and codes inside the visit for this batch
        pad_length_visits = max(map(len, x_codes))
        pad_length_codes = max(map(lambda x: max(map(len, x)), x_codes))
        #Number of elements in a batch (useful in case of partial batches)
        length_batch = len(x_codes)
        #Pad data
        x_codes = pad_data(x_codes, pad_length_visits, pad_length_codes, self.num_codes)
        outputs = [x_codes]
        #Add numeric data if necessary
        if self.numeric_size:
            x_numeric = self.numeric[batch_slice]
            x_numeric = pad_data(x_numeric, pad_length_visits, self.numeric_size, -99.0)
            outputs.append(x_numeric)
        #Add time data if necessary
        if self.use_time:
            x_time = sequence.pad_sequences(self.time[batch_slice],
                                            dtype=np.float32, maxlen=pad_length_visits,
                                            value=+99).reshape(length_batch, pad_length_visits, 1)
            outputs.append(x_time)

        return outputs

    def all_patient_data(self):
        for i in range(0, self.__len__()):
            yield self.__getitem__(i)


def read_data(model_parameters, path_data, path_dictionary):
    """Read the data from provided paths and assign it into lists"""
    data = pd.read_pickle(path_data)
    data_output = [data['codes'].values]

    if model_parameters.numeric_size:
        data_output.append(data['numerics'].values)
    if model_parameters.use_time:
        data_output.append(data['to_event'].values)

    with open(path_dictionary, 'rb') as f:
        dictionary = pickle.load(f)

    dictionary[model_parameters.num_codes] = 'PADDING'
    return data['PID'], data_output, dictionary, data['target']

def get_importances(alphas, betas, patient_data, model_parameters, dictionary):
    """Construct dataframes that interpret each visit of the given patient"""
    importances = []
    codes = patient_data[0][0]
    index = 1
    if model_parameters.numeric_size:
        numerics = patient_data[index][0]
        index += 1

    if model_parameters.use_time:
        time = patient_data[index][0].reshape((len(codes),))
    else:
        time = np.arange(len(codes))
    for i in range(len(patient_data[0][0])):
        visit_codes = codes[i]
        visit_beta = betas[i]
        visit_alpha = alphas[i][0]
        relevant_indices = np.append(visit_codes,
                                     range(model_parameters.num_codes+1,
                                           model_parameters.num_codes+1+model_parameters.numeric_size))\
                                          .astype(np.int32)
        values = np.full(fill_value='Diagnosed', shape=(len(visit_codes),))
        if model_parameters.numeric_size:
            visit_numerics = numerics[i]
            values = np.append(values, visit_numerics)
        values_mask = np.array([1. if value == 'Diagnosed' else value for value in values], dtype=np.float32)
        beta_scaled = visit_beta * model_parameters.emb_weights[relevant_indices]
        output_scaled = np.dot(beta_scaled, model_parameters.output_weights)
        alpha_scaled = values_mask * visit_alpha * output_scaled
        df_visit = pd.DataFrame({'status':values,
                                 'feature': [dictionary[index] for index in relevant_indices],
                                 'importance_feature':alpha_scaled[:, 0],
                                 'importance_visit':visit_alpha,
                                 'to_event':time[i]},
                                columns=['status', 'feature', 'importance_feature',
                                         'importance_visit', 'to_event'])
        df_visit = df_visit[df_visit['feature'] != 'PADDING']
        df_visit.sort_values(['importance_feature'], ascending=False, inplace=True)
        importances.append(df_visit)

    return importances

def get_predictions(model, data, model_parameters, ARGS):
    """Get Model Predictions"""
    test_generator = SequenceBuilder(data, model_parameters, ARGS)
    preds = model.predict_generator(generator=test_generator, max_queue_size=15,
                                    use_multiprocessing=True, verbose=1, workers=3)
    return preds

def bayesian_average(df):
    R = df.mean(axis=1)
    C = df.sum(axis=1).sum()/df.count(axis=1).sum()
    w = df.count(axis=1)/(df.count(axis=1)+df.count(axis=1).mean())
    return ((w*R) + ((1-w)*C))

def main(ARGS):
    """Main Body of the code"""
    print('Loading Model and Extracting Parameters')
    model, model_with_attention = import_model(ARGS.path_model)
    model_parameters = get_model_parameters(model)
    print('Reading Data')
    pid, data, dictionary, target = read_data(model_parameters, ARGS.path_data, ARGS.path_dictionary)
    data_generator = SequenceBuilder(data, model_parameters, ARGS)
    probabilities = get_predictions(model, data, model_parameters, ARGS)
    ARGS.batch_size = 1
    data_generator = SequenceBuilder(data, model_parameters, ARGS)
    if ARGS.avg:
        print("Calculating average feature weights for the given model/data (this could take a while...)")
        mean_avgs = pd.DataFrame(index=dictionary.values())
        ba_avgs = pd.DataFrame(index=dictionary.values())
        mean_avgs0, mean_avgs1 = pd.DataFrame(index=dictionary.values()), pd.DataFrame(index=dictionary.values())
        ba_avgs0, ba_avgs1 = pd.DataFrame(index=dictionary.values()), pd.DataFrame(index=dictionary.values())
        res = []
        res_last_day = []
        res_one = []
        res_zero = []
        for i in tqdm(range(data_generator.__len__())):
            patient_data = data_generator.__getitem__(i)
            proba, alphas, betas = model_with_attention.predict_on_batch(patient_data)
            visits = get_importances(alphas[0], betas[0], patient_data, model_parameters, dictionary)
            for visit in visits:
                visit['idx'] = i
                visit['PID'] = pid.iloc[i]
                res.append(visit.iloc[0][3:])
            res_last_day.append(visits[-1])
            feature_values = pd.concat([visit.set_index('feature')['importance_feature'] for visit in visits], axis=1, sort=True)
            # averaged across all visits
            mean_avgs['patient_{}'.format(i)] = feature_values.mean(axis=1)
            ba_avgs['patient_{}'.format(i)] = bayesian_average(feature_values)
                # update progress bar
                #pbar.update(1)
            if ARGS.seperate:
                
                if target.iloc[i] == 1:
                    feature_values1 = pd.concat([visit.set_index('feature')['importance_feature'] for visit in visits], axis=1, sort=True)
                    # averaged across all visits
                    mean_avgs1['patient_{}'.format(i)] = feature_values1.mean(axis=1)
                    ba_avgs1['patient_{}'.format(i)] = bayesian_average(feature_values1)
                if target.iloc[i] == 0:
                    feature_values0 = pd.concat([visit.set_index('feature')['importance_feature'] for visit in visits], axis=1, sort=True)
                    # averaged across all visits
                    mean_avgs0['patient_{}'.format(i)] = feature_values0.mean(axis=1)
                    ba_avgs0['patient_{}'.format(i)] = bayesian_average(feature_values0)
            
        # averaged across all patients
        full_avgs = pd.DataFrame(index=dictionary.values())
        full_avgs['Mean'] = mean_avgs.mean(axis=1)
        full_avgs['Bayes'] = bayesian_average(ba_avgs)
        full_avgs.dropna(how='all', inplace=True)
        time_name = datetime.datetime.now()
        full_avgs.to_pickle('feature_weights_{}.pkl'.format(time_name))

        print(full_avgs.to_string())


        if ARGS.seperate:
            # averaged across all patients
            full_avgs1 = pd.DataFrame(index=dictionary.values())
            full_avgs1['Mean'] = mean_avgs1.mean(axis=1)
            full_avgs1['Bayes'] = bayesian_average(ba_avgs1)
            full_avgs1.dropna(how='all', inplace=True)
            time_name1 = datetime.datetime.now()
            full_avgs1.to_pickle('feature_weights_with_target_1_{}.pkl'.format(time_name1))

            print(full_avgs1.to_string())

            # averaged across all patients
            full_avgs0 = pd.DataFrame(index=dictionary.values())
            full_avgs0['Mean'] = mean_avgs0.mean(axis=1)
            full_avgs0['Bayes'] = bayesian_average(ba_avgs0)
            full_avgs0.dropna(how='all', inplace=True)
            time_name0 = datetime.datetime.now()
            full_avgs0.to_pickle('feature_weights_with_target_0_{}.pkl'.format(time_name0))

            print(full_avgs0.to_string())

        if ARGS.lastday:
            df_last_day = pd.concat(res_last_day)
            time_label = datetime.datetime.now()
            df_last_day.to_pickle('features_weights_last_day_{}.pkl'.format(time_label))
        if ARGS.fullrun:
            res_df = pd.DataFrame(res)
            print(res_df)
            time_label = datetime.datetime.now()
            res_df.to_csv('./inter_res/fullrun-{}.csv'.format(time_label))
            print('file is saved in ./inter_res/fullrun-{}.csv'.format(time_label))

    if ARGS.id:
         while 1:
             patient_id = int(input('Input Patient Order Number: '))
             if patient_id > len(data[0]) - 1:
                 print('Invalid ID, there are only {} patients'.format(len(data[0])))
             elif patient_id < 0:
                 print('Only Positive IDs are accepted')
             else:
                 print('Patients probability: {}'.format(probabilities[patient_id, 0, 0]))
                 proceed = str(input('Output predictions? (y/n): '))
                 if proceed == 'y':
                     patient_data = data_generator.__getitem__(patient_id)
                     proba, alphas, betas = model_with_attention.predict_on_batch(patient_data)
                     visits = get_importances(alphas[0], betas[0], patient_data, model_parameters, dictionary)
                     one_df = pd.concat(visits)
                     time_label = datetime.datetime.now()
                     one_df.to_csv('./inter_res/{}_results_{}.csv'.format(patient_id,time_label))
                     print('file is saved in ./inter_res/{}_results_{}.csv'.format(patient_id,time_label))

def parse_arguments(parser):
    """Read user arguments"""
    parser.add_argument('--path_model',
                        type=str, default='Model/weights.01.hdf5',
                        help='Path to the model to evaluate')
    parser.add_argument('--path_data', type=str, default='data/data_test.pkl',
                        help='Path to evaluation data')
    parser.add_argument('--path_dictionary', type=str, default='data/dictionary.pkl',
                        help='Path to codes dictionary')
    parser.add_argument('--batch_size', type=int, default=32,
                        help='Batch size for initial probability predictions')
    parser.add_argument('--avg', action='store_true',
                        help='if set, compiles avg of features weights across all patients/visits')
    parser.add_argument('--id', action='store_true',
                         help='Id of the patient being interpreted')
    parser.add_argument('--fullrun',action='store_true', help='if set output to the csv file of all patient result with patient_id, importance_visit, to_event')
    parser.add_argument('--lastday',action='store_true')
    parser.add_argument('--seperate',action='store_true')
    args = parser.parse_args()

    return args

if __name__ == '__main__':

    PARSER = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ARGS = parse_arguments(PARSER)
    main(ARGS)
