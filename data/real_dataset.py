import os
import numpy as np
import pandas as pd
import torch
import random
import json
from torch.utils.data import Dataset, DataLoader
from statsmodels.tsa.seasonal import seasonal_decompose, STL

DATA_DIRS = '/mnt/infonas/data/pratham/Forecasting/DILATE'

def generate_train_dev_test_data(data, N_input):
	train_per = 0.6
	dev_per = 0.2
	N = len(data)

	data_train = data[:int(train_per*N)]
	data_dev = data[int(train_per*N)-N_input:int((train_per+dev_per)*N)]
	data_test = data[int((train_per+dev_per)*N)-N_input:]

	return  (data_train, data_dev, data_test)

def create_forecast_io_seqs(data, enc_len, dec_len, stride):

	data_in, data_out = [], []
	for idx in range(0, len(data), stride):
		if idx+enc_len+dec_len <= len(data):
			data_in.append(data[idx:idx+enc_len])
			data_out.append(data[idx+enc_len:idx+enc_len+dec_len])

	data_in = np.array(data_in)
	data_out = np.array(data_out)
	return data_in, data_out


def process_start_string(start_string, freq):
	'''
	Source: 
	https://github.com/mbohlkeschneider/gluon-ts/blob/442bd4ffffa4a0fcf9ae7aa25db9632fbe58a7ea/src/gluonts/dataset/common.py#L306
	'''

	timestamp = pd.Timestamp(start_string, freq=freq)
	# 'W-SUN' is the standardized freqstr for W
	if timestamp.freq.name in ("M", "W-SUN"):
	    offset = to_offset(freq)
	    timestamp = timestamp.replace(
	        hour=0, minute=0, second=0, microsecond=0, nanosecond=0
	    )
	    return pd.Timestamp(
	        offset.rollback(timestamp), freq=offset.freqstr
	    )
	if timestamp.freq == 'B':
	    # does not floor on business day as it is not allowed
	    return timestamp
	return pd.Timestamp(
	    timestamp.floor(timestamp.freq), freq=timestamp.freq
	)

def shift_timestamp(ts, offset):
	result = ts + offset * ts.freq
	return pd.Timestamp(result, freq=ts.freq)

def get_date_range(start_string, freq, seq_len):
	start = process_start_string(start_string, freq)
	end = shift_timestamp(start, seq_len)
	full_date_range = pd.date_range(start, end, freq=freq)
	return full_date_range


def get_list_of_dict_format(data):
	data_new = list()
	for entry in data:
		entry_dict = dict()
		entry_dict['target'] = entry
		data_new.append(entry_dict)
	return data_new

def decompose_seq(seq, decompose_type, period, N_output, is_train):
    if is_train:
        if decompose_type == 'seasonal':
            components = seasonal_decompose(
               seq, model='additive', period=period, extrapolate_trend=True
            )
            coeffs = torch.tensor(
                [components.trend, components.seasonal, components.resid]
            ).transpose(0,1)
        elif decompose_type == 'STL':
            stl_components = STL(seq, period=period).fit()
            coeffs = torch.tensor(
                [stl_components.trend, stl_components.seasonal, stl_components.resid]
            ).transpose(0,1)
        #coeffs = torch.log(coeffs)
        coeffs = (coeffs - coeffs.mean(dim=-1, keepdims=True)) / coeffs.std(dim=-1, keepdims=True)
    else:
        seq_tr = seq[:-N_output]
        seq_out = seq[-N_output:]
        if decompose_type == 'seasonal':
            components_tr = seasonal_decompose(
               seq_tr, model='additive', period=period, extrapolate_trend=True
            )
            #components_out = seasonal_decompose(
            #   seq_out, model='additive', period=period, extrapolate_trend=True
            #)
            coeffs_tr = torch.tensor([components_tr.trend, components_tr.seasonal, components_tr.resid]).transpose(0,1)
            #coeffs_out = torch.tensor([components.trend, components.seasonal, components.resid]).transpose(0,1)
        elif decompose_type == 'STL':
            stl_tr = STL(seq_tr, period=period).fit()
            #stl_out = STL(seq_out, period=period).fit()
            coeffs_tr = torch.tensor([stl_tr.trend, stl_tr.seasonal, stl_tr.resid]).transpose(0,1)
            #coeffs_out = torch.tensor([stl_out.trend, stl_out.seasonal, stl_out.resid]).transpose(0,1)

        means = coeffs_tr.mean(dim=0, keepdims=True)
        stds = coeffs_tr.std(dim=0, keepdims=True)
        coeffs_tr = (coeffs_tr - means) / stds
        coeffs_out = torch.zeros([seq_out.shape[0], coeffs_tr.shape[1]], dtype=torch.float)
        coeffs = torch.cat([coeffs_tr, coeffs_out], dim=0)
        #coeffs = torch.log(coeffs)

    return coeffs


def parse_Traffic(N_input, N_output):
    with open(os.path.join(DATA_DIRS, 'data/traffic/traffic.txt'), 'r') as f:
        data = []
        # Taking only first series of length 17544
        # TODO: Add all series to the dataset
        for line in f:
        	data.append(line.rstrip().split(',')[0])
        data = np.array(data).astype(np.float32)
        data = np.expand_dims(np.expand_dims(data, axis=-1), axis=0)

    #data_train, data_dev, data_test = generate_train_dev_test_data(data, N_input)
    
    train_len = int(0.6 * data.shape[1])
    dev_len = int(0.2 * data.shape[1])
    test_len = data.shape[1] - train_len - dev_len
    
    data_train = data[:, :train_len]
    
    data_dev, data_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    for i in range(data.shape[0]):
        for j in range(train_len, train_len+dev_len, N_output):
            data_dev.append(data[i, :j])
            dev_tsid_map[len(data_dev)-1] = i
    for i in range(data.shape[0]):
        for j in range(train_len+dev_len, data.shape[1], N_output):
            data_test.append(data[i, :j])
            test_tsid_map[len(data_test)-1] = i

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)
    
    return (
    	data_train, data_dev, data_test, dev_tsid_map, test_tsid_map
    )

def parse_ECG5000(N_input, N_output):
	with open(os.path.join(DATA_DIRS, 'data/ECG5000/ECG5000_TRAIN.tsv'), 'r') as f:
		data = []
		for line in f:
			data.append(line.rstrip().split())
		data = np.array(data).astype(np.float32)
		data = np.expand_dims(data, axis=-1)
	with open(os.path.join(DATA_DIRS, 'data/ECG5000/ECG5000_TEST.tsv'), 'r') as f:
		data_test = []
		for line in f:
			data_test.append(line.rstrip().split())
		data_test = np.array(data_test).astype(np.float32)
		data_test = np.expand_dims(data_test, axis=-1)

	N = data.shape[0]
	dev_len = int(0.2*N)
	train_len = N - dev_len
	data_train, data_dev = data[:train_len], data[train_len:train_len+dev_len]

	data_train_in, data_train_out = data_train[:, :N_input], data_train[:, N_input:N_input+N_output]
	data_dev_in, data_dev_out = data_dev[:, :N_input], data_dev[:, N_input:N_input+N_output]
	data_test_in, data_test_out = data_test[:, :N_input], data_test[:, N_input:N_input+N_output]

	train_bkp = np.ones(data_train_in.shape[0]) * N_input
	dev_bkp = np.ones(data_dev_in.shape[0]) * N_input
	test_bkp = np.ones(data_test_in.shape[0]) * N_input

	data_train = get_list_of_dict_format(data_train)
	data_dev = get_list_of_dict_format(data_dev)
	data_test = get_list_of_dict_format(data_test)

	return (
		data_train_in, data_train_out, data_dev_in, data_dev_out,
		data_test_in, data_test_out, train_bkp, dev_bkp, test_bkp,
		data_train, data_dev, data_test
	)

def create_bins(sequence, bin_size, num_bins):
	#num_bins = int(np.ceil((sequence[-1] - sequence[0]) * 1. / bin_size))
	counts = [0. for _ in range(num_bins)]
	curr_cnt = 0
	for ts in sequence:
		bin_id = int(ts // bin_size)
		counts[bin_id] += 1

	return counts

def parse_Taxi(N_input, N_output):
	# https://s3.amazonaws.com/nyc-tlc/trip+data/yellow_tripdata_2019-01.csv
	# https://s3.amazonaws.com/nyc-tlc/trip+data/yellow_tripdata_2019-02.csv
	taxi_df_jan = pd.read_csv(
		'data/yellow_tripdata_2019-01.csv',
		usecols=["tpep_pickup_datetime", "PULocationID"])
	taxi_df_feb = pd.read_csv(
		'data/yellow_tripdata_2019-02.csv',
		usecols=["tpep_pickup_datetime", "PULocationID"])
	taxi_df = taxi_df_jan.append(taxi_df_feb)
	taxi_df['tpep_pickup_datetime'] = pd.to_datetime(
		taxi_df['tpep_pickup_datetime'], format='%Y-%m-%d %H:%M:%S', errors='coerce'
	)
	## Data cleaning
	# Dataset contains some spurious values, such as year 2038 and months other
	# than Jan and Feb. Following code purges such rows.
	taxi_df = taxi_df[(taxi_df['tpep_pickup_datetime'].dt.year == 2019)]
	taxi_df = taxi_df[(taxi_df['tpep_pickup_datetime'].dt.month < 3)]

	taxi_df = taxi_df.sort_values('tpep_pickup_datetime')
	taxi_df['timestamp'] = pd.DatetimeIndex(taxi_df['tpep_pickup_datetime']).astype(np.int64)/1000000000
	del taxi_df['tpep_pickup_datetime']
	taxi_df = taxi_df.sort_values(by=['timestamp'])
	#dataset_name = 'taxi'
	#if dataset_name in downsampling:
	#	taxi_timestamps = downsampling_dataset(taxi_timestamps, dataset_name)

	num_hrs = int(np.ceil((taxi_df['timestamp'].values[-1] - taxi_df['timestamp'].values[0])/3600.))
	loc2counts = dict()
	loc2numevents = dict()
	loc2startts = dict()
	for loc_id, loc_df in taxi_df.groupby(['PULocationID']):
		timestamps = loc_df['timestamp'].values
		timestamps = timestamps - timestamps[0]
		loc2numevents[loc_id] = len(timestamps)
		# Select locations in which num_events per hour is >1
		if (len(timestamps) >= N_input+N_output and len(timestamps) / num_hrs > 1.):
			counts = create_bins(timestamps, bin_size=3600., num_bins=num_hrs)
			print(loc_id, len(timestamps), len(timestamps) / num_hrs, len(counts))
			loc2counts[loc_id] = counts

			#start_ts = pd.Timestamp(loc_df['timestamp'][0], unit='s')
			#loc2startts = start_ts

	data = np.array([val for val in loc2counts.values()])
	data = np.expand_dims(data, axis=2)
	data_train, data_dev, data_test = [], [], []
	data_train_in, data_train_out = [], []
	data_dev_in, data_dev_out = [], []
	data_test_in, data_test_out = [], []
	for seq in data:
		seq_train, seq_dev, seq_test = generate_train_dev_test_data(seq, N_input)
		batch_train_in, batch_train_out = create_forecast_io_seqs(seq_train, N_input, N_output, int(N_output/3))
		batch_dev_in, batch_dev_out = create_forecast_io_seqs(seq_dev, N_input, N_output, N_output)
		batch_test_in, batch_test_out = create_forecast_io_seqs(seq_test, N_input, N_output, N_output)
		data_train.append(seq_train)
		data_dev.append(seq_dev)
		data_test.append(seq_test)
		data_train_in.append(batch_train_in)
		data_train_out.append(batch_train_out)
		data_dev_in.append(batch_dev_in)
		data_dev_out.append(batch_dev_out)
		data_test_in.append(batch_test_in)
		data_test_out.append(batch_test_out)

	data_train_in = np.concatenate(data_train_in, axis=0)
	data_train_out = np.concatenate(data_train_out, axis=0)
	data_dev_in = np.concatenate(data_dev_in, axis=0)
	data_dev_out = np.concatenate(data_dev_out, axis=0)
	data_test_in = np.concatenate(data_test_in, axis=0)
	data_test_out = np.concatenate(data_test_out, axis=0)

	train_bkp = np.ones(data_train_in.shape[0]) * N_input
	dev_bkp = np.ones(data_dev_in.shape[0]) * N_input
	test_bkp = np.ones(data_test_in.shape[0]) * N_input

	data_train = get_list_of_dict_format(data_train)
	data_dev = get_list_of_dict_format(data_dev)
	data_test = get_list_of_dict_format(data_test)

	return (
		data_train_in, data_train_out, data_dev_in, data_dev_out,
		data_test_in, data_test_out, train_bkp, dev_bkp, test_bkp,
		data_train, data_dev, data_test
	)

def parse_Traffic911_bak(N_input, N_output):
	call_df = pd.read_csv('data/911.csv')
	call_df = call_df[call_df['zip'].isnull()==False] # Ignore calls with NaN zip codes
	print('Types of Emergencies')
	print(call_df.title.apply(lambda x: x.split(':')[0]).value_counts())
	call_df['type'] = call_df.title.apply(lambda x: x.split(':')[0])
	print('Subtypes')
	for each in call_df.type.unique():
	    subtype_count = call_df[call_df.title.apply(lambda x: x.split(':')[0]==each)].title.value_counts()
	    print('For', each, 'type of Emergency, we have ', subtype_count.count(), 'subtypes')
	    print(subtype_count[subtype_count>100])
	print('Out of 3 types, considering only Traffic')
	call_data = call_df[call_df['type']=='Traffic']
	call_data['timeStamp'] = pd.to_datetime(call_data['timeStamp'], errors='coerce')
	print("We have timeline from", call_data['timeStamp'].min(), "to", call_data['timeStamp'].max())
	call_data = call_data.sort_values('timeStamp')
	call_data['timeStamp'] = pd.DatetimeIndex(call_data['timeStamp']).astype(np.int64)/1000000000

	num_hrs = int(
		np.ceil(
			(call_data['timeStamp'].values[-1] - call_data['timeStamp'].values[0])/(3600.)
		)
	)
	timestamps = call_data['timeStamp'].values
	timestamps = timestamps - timestamps[0]
	counts = create_bins(timestamps, bin_size=3600., num_bins=num_hrs)
	data = np.expand_dims(np.array(counts), axis=0)
	data = np.expand_dims(data, axis=2)
	data_train, data_dev, data_test = [], [], []
	data_train_in, data_train_out = [], []
	data_dev_in, data_dev_out = [], []
	data_test_in, data_test_out = [], []
	for seq in data:
		seq_train, seq_dev, seq_test = generate_train_dev_test_data(seq, N_input)
		batch_train_in, batch_train_out = create_forecast_io_seqs(seq_train, N_input, N_output, int(N_output/3))
		batch_dev_in, batch_dev_out = create_forecast_io_seqs(seq_dev, N_input, N_output, N_output)
		batch_test_in, batch_test_out = create_forecast_io_seqs(seq_test, N_input, N_output, N_output)
		data_train.append(seq_train)
		data_dev.append(seq_dev)
		data_test.append(seq_test)
		data_train_in.append(batch_train_in)
		data_train_out.append(batch_train_out)
		data_dev_in.append(batch_dev_in)
		data_dev_out.append(batch_dev_out)
		data_test_in.append(batch_test_in)
		data_test_out.append(batch_test_out)

	data_train_in = np.concatenate(data_train_in, axis=0)
	data_train_out = np.concatenate(data_train_out, axis=0)
	data_dev_in = np.concatenate(data_dev_in, axis=0)
	data_dev_out = np.concatenate(data_dev_out, axis=0)
	data_test_in = np.concatenate(data_test_in, axis=0)
	data_test_out = np.concatenate(data_test_out, axis=0)

	train_bkp = np.ones(data_train_in.shape[0]) * N_input
	dev_bkp = np.ones(data_dev_in.shape[0]) * N_input
	test_bkp = np.ones(data_test_in.shape[0]) * N_input

	data_train = get_list_of_dict_format(data_train)
	data_dev = get_list_of_dict_format(data_dev)
	data_test = get_list_of_dict_format(data_test)

	return (
		data_train_in, data_train_out, data_dev_in, data_dev_out,
		data_test_in, data_test_out, train_bkp, dev_bkp, test_bkp,
		data_train, data_dev, data_test
	)

def parse_gc_datasets(dataset_name, N_input, N_output):


	if dataset_name in ['Exchange']:
		num_rolling_windows = 5
		num_val_rolling_windows = 2
		dataset_dir = 'exchange_rate_nips'
	elif dataset_name in ['Wiki']:
		num_rolling_windows = 5
		num_val_rolling_windows = 2
		dataset_dir = 'wiki-rolling_nips'
	elif dataset_name in ['Solar']:
		num_rolling_windows = 7
		num_val_rolling_windows = 2
		dataset_dir = 'solar_nips'
	elif dataset_name in ['taxi30min']:
		num_rolling_windows = 7
		num_val_rolling_windows = 2
		dataset_dir = 'taxi_30min'

	data_ = []
	with open(os.path.join(DATA_DIRS, 'data', dataset_dir, 'train', 'train.json')) as f:
		for line in f:
			data_.append(json.loads(line))

	data_test_full_ = []
	with open(os.path.join(DATA_DIRS, 'data', dataset_dir, 'test', 'test.json')) as f:
		for line in f:
			data_test_full_.append(json.loads(line))

	if dataset_name in ['Wiki']:
		num_ts = len(data_)
		data = data_[ -2000 : ]
		data_test_full = []
		for i in range(0, num_ts*num_rolling_windows, num_ts):
			data_test_full += data_test_full_[ i : i+num_ts ][ -2000 : ]
	elif dataset_name in ['taxi30min']:
		data = data_
		num_ts = 1214 * num_rolling_windows
		data_test_full = data_test_full_[ -num_ts : ]
		for i in range(len(data_test_full)):
			assert data[i % len(data)]['lat'] == data_test_full[i]['lat']
			assert data[i % len(data)]['lng'] == data_test_full[i]['lng']
			data_test_full[i]['target'] = data[i % len(data)]['target'] + data_test_full[i]['target']
			data_test_full[i]['start'] = data[i % len(data)]['start']
	else:
		data = data_
		data_test_full = data_test_full_

	metadata = json.load(open(os.path.join(DATA_DIRS, 'data', dataset_dir, 'metadata', 'metadata.json')))


	data_train, data_dev, data_test = [], [], []
	dev_tsid_map, test_tsid_map = {}, {}
	data_train_in, data_train_out = [], []
	data_dev_in, data_dev_out = [], []
	data_test_in, data_test_out = [], []
	for i, entry in enumerate(data, 0):
		entry_train = dict()

		train_len = len(entry['target']) - N_output*num_val_rolling_windows
		seq_train = entry['target'][ : train_len ]
		seq_train = np.expand_dims(seq_train, axis=-1)

		seq_dates = get_date_range(entry['start'], metadata['time_granularity'], len(entry['target']))
		start_train = seq_dates[0]

		entry_train['target'] = seq_train
		entry_train['start'] = start_train
		entry_train['freq_str'] = metadata['time_granularity']

		data_train.append(entry_train)

		for j in range(train_len+N_output, len(entry['target'])+1, N_output):
			entry_dev = {}
			seq_dev = entry['target'][:j]
			seq_dev = np.expand_dims(seq_dev, axis=-1)

			start_dev = seq_dates[0]

			entry_dev['target'] = seq_dev
			entry_dev['start'] = start_dev
			entry_dev['freq_str'] = metadata['time_granularity']
			data_dev.append(entry_dev)
			dev_tsid_map[len(data_dev)-1] = i

	for i, entry in enumerate(data_test_full, 0):
		entry_test = dict()
		seq_test = entry['target']
		seq_test = np.expand_dims(seq_test, axis=-1)

		seq_dates = get_date_range(entry['start'], metadata['time_granularity'], len(entry['target']))
		start_test = seq_dates[0]

		entry_test['target'] = seq_test
		entry_test['start'] = start_test
		entry_test['freq_str'] = metadata['time_granularity']
		data_test.append(entry_test)
		test_tsid_map[i] = i%len(data) # Multiple test instances per train series.

	if data_dev == []:
		data_dev = data_test
		dev_tsid_map = test_tsid_map
	return (
		data_train, data_dev, data_test, dev_tsid_map, test_tsid_map
	)

def parse_weather(dataset_name, N_input, N_output):

        csv_path = os.path.join(DATA_DIRS, 'data', 'jena_climate_2009_2016.csv')
        df = pd.read_csv(csv_path)
        df = df[5::6] # Sub-sample the data from 10minute interval to 1h
        df = df[['T (degC)']] # Select temperature column, 'T (degC)'
        df = df.values.T # Retain only values in np format
        df = np.expand_dims(df, axis=-1)
        n = df.shape[1]

        # Split the data - train, dev, and test
        train_len = int(n*0.8)
        dev_len = int(n*0.1)
        test_len = n - (train_len + dev_len)
        data_train = df[:, 0:train_len]

        data_dev, data_test = [], []
        dev_tsid_map, test_tsid_map = {}, {}
        for i in range(df.shape[0]):
            for j in range(train_len, train_len+dev_len, N_output):
                data_dev.append(df[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
        for i in range(df.shape[0]):
            for j in range(train_len+dev_len, n, N_output):
                data_test.append(df[i, :j])
                test_tsid_map[len(data_test)-1] = i
        data_train = get_list_of_dict_format(data_train)
        data_dev = get_list_of_dict_format(data_dev)
        data_test = get_list_of_dict_format(data_test)

        return (
                data_train, data_dev, data_test, dev_tsid_map, test_tsid_map
        )


def parse_bafu(dataset_name, N_input, N_output):
    file_path = os.path.join(DATA_DIRS, 'data', 'bafu_normal.txt')
    data = np.loadtxt(file_path)
    data = data.T
    data = np.expand_dims(data, axis=-1)

    n = data.shape[1]
    test_len = 48*28*7
    dev_len = 48*28*2
    train_len = n - dev_len - test_len

    data_train = data[:, :train_len]

    data_dev, data_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
#    for i in range(data.shape[0]):
#        for j in range(train_len, train_len+dev_len, 1):
#            data_dev.append(data[i, :j])
#            dev_tsid_map[len(data_dev)-1] = i
#    for i in range(data.shape[0]):
#        for j in range(train_len+dev_len, n, N_output):
#            data_test.append(data[i, :j])
#            test_tsid_map[len(data_test)-1] = i

    for i in range(data.shape[0]):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= train_len+dev_len:
                data_dev.append(data[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
    #for i in range(data.shape[0]):
    for i in range(data.shape[0]):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                test_tsid_map[len(data_test)-1] = i

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    return (
            data_train, data_dev, data_test, dev_tsid_map, test_tsid_map
    )


def parse_meteo(dataset_name, N_input, N_output):
    file_path = os.path.join(DATA_DIRS, 'data', 'meteo_normal.txt')
    data = np.loadtxt(file_path)
    data = data.T
    data = np.expand_dims(data, axis=-1)

    n = data.shape[1]
    test_len = 2000
    dev_len = 1000
    train_len = n - dev_len - test_len

    data_train = data[:, :train_len]

    data_dev, data_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    for i in range(data.shape[0]):
        for j in range(train_len, train_len+dev_len, 1):
            data_dev.append(data[i, :j])
            dev_tsid_map[len(data_dev)-1] = i
    for i in range(data.shape[0]):
        for j in range(train_len+dev_len, n, N_output):
            data_test.append(data[i, :j])
            test_tsid_map[len(data_test)-1] = i

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    return (
            data_train, data_dev, data_test, dev_tsid_map, test_tsid_map
    )

def parse_azure_bak(dataset_name, N_input, N_output):
    file_path = os.path.join(DATA_DIRS, 'data', 'azure.npy')
    data = np.load(file_path)
    data = np.expand_dims(data, axis=-1)

    n = data.shape[1]
    test_len = 60*24*2
    dev_len = 60*24
    train_len = n - dev_len - test_len

    data_train = data[:, :train_len]

    data_dev, data_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    #for i in range(data.shape[0]):
    for i in range(2, 3):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= train_len+dev_len:
                data_dev.append(data[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
    #for i in range(data.shape[0]):
    for i in range(2, 3):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                test_tsid_map[len(data_test)-1] = i

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    return (
            data_train, data_dev, data_test, dev_tsid_map, test_tsid_map
    )


def parse_azure(dataset_name, N_input, N_output):
    file_path = os.path.join(DATA_DIRS, 'data', 'azure.npy')
    data = np.load(file_path)
    data = torch.tensor(data, dtype=torch.float)
    #data = np.expand_dims(data, axis=-1)

    n = data.shape[1]
    test_len = 60*24*2
    dev_len = 60*24*2
    train_len = n - dev_len - test_len

    feats = np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 60
    feats = np.expand_dims(feats, axis=-1)
    feats = torch.tensor(feats, dtype=torch.float)

    data_train = data[:, :train_len]
    feats_train = feats[:, :train_len]

    data_dev, data_test = [], []
    feats_dev, feats_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    for i in range(data.shape[0]):
    #for i in range(2, 3):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= train_len+dev_len:
                data_dev.append(data[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
                feats_dev.append(feats[i, :j])
    for i in range(data.shape[0]):
    #for i in range(2, 3):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                test_tsid_map[len(data_test)-1] = i
                feats_test.append(feats[i, :j])

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    # Add time-features
    for i in range(len(data_train)):
        #seq_dates = get_date_range("2021-01-01 00:00:00", '1min', len(data_train[i]['target']))
        #data_train[i]['freq_str'] = '1min'
        #data_train[i]['start'] = seq_dates[0]
        data_train[i]['feats'] = feats_train[i]
        data_train[i]['coeffs'] = torch.zeros((len(data_train[i]['target']), 1), dtype=torch.float)
    for i in range(len(data_dev)):
        #seq_dates = get_date_range("2021-01-01 00:00:00", '1min', len(data_dev[i]['target']))
        #data_dev[i]['freq_str'] = '1min'
        #data_dev[i]['start'] = seq_dates[0]
        data_dev[i]['feats'] = feats_dev[i]
        data_dev[i]['coeffs'] = torch.zeros((len(data_dev[i]['target']), 1), dtype=torch.float)
    for i in range(len(data_test)):
        #seq_dates = get_date_range("2021-01-01 00:00:00", '1min', len(data_test[i]['target']))
        #data_test[i]['freq_str'] = '1min'
        #data_test[i]['start'] = seq_dates[0]
        data_test[i]['feats'] = feats_test[i]
        data_test[i]['coeffs'] = torch.zeros((len(data_test[i]['target']), 1), dtype=torch.float)


    feats_info = {0:(60, 32)}
    coeffs_info = {0: (0, 1)}

    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )


def parse_ett_bak(dataset_name, N_input, N_output):
    dataset_path = os.path.join(DATA_DIRS, 'data', 'ETT')
    data_train = np.load(os.path.join(dataset_path, 'oilTemp_train2.npy')).T
    data_dev = np.load(os.path.join(dataset_path, 'oilTemp_dev2.npy')).T
    data_test = np.load(os.path.join(dataset_path, 'oilTemp_test2.npy')).T
    data_train = np.expand_dims(data_train, axis=-1)
    data_dev = np.expand_dims(data_dev, axis=-1)
    data_test = np.expand_dims(data_test, axis=-1)
    data_dev = data_dev[:, N_input:]
    data_test = data_test[:, N_input:]

    #import ipdb
    #ipdb.set_trace()

    n = data_train.shape[1] + data_dev.shape[1] + data_test.shape[1]
    test_len = data_test.shape[1]
    dev_len = data_test.shape[1]
    train_len = n - test_len - dev_len

    data = np.concatenate([data_train, data_dev, data_test], axis=1)

    data_dev, data_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    for i in range(data.shape[0]):
    #for i in range(2, 3):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= train_len+dev_len:
                data_dev.append(data[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
    for i in range(data.shape[0]):
    #for i in range(2, 3):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                test_tsid_map[len(data_test)-1] = i

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    # Add time-features
    for i in range(len(data_train)):
        seq_dates = get_date_range("2021-01-01 00:00:00", '1min', len(data_train[i]['target']))
        data_train[i]['freq_str'] = '51min'
        data_train[i]['start'] = seq_dates[0]
    for i in range(len(data_dev)):
        seq_dates = get_date_range("2021-01-01 00:00:00", '1min', len(data_dev[i]['target']))
        data_dev[i]['freq_str'] = '15min'
        data_dev[i]['start'] = seq_dates[0]
    for i in range(len(data_test)):
        seq_dates = get_date_range("2021-01-01 00:00:00", '1min', len(data_test[i]['target']))
        data_test[i]['freq_str'] = '15min'
        data_test[i]['start'] = seq_dates[0]

    return (
            data_train, data_dev, data_test, dev_tsid_map, test_tsid_map
    )


def parse_ett(dataset_name, N_input, N_output):
    df = pd.read_csv('/mnt/infonas/data/pbansal/ETTm1.csv')
    data = df[['OT']].to_numpy().T
    #data = np.expand_dims(data, axis=-1)

    n = data.shape[1]
    train_len = int(0.7*n)
    dev_len = int(0.1*n)
    test_len = n - train_len - dev_len

    feats_cont = np.expand_dims(df[['HUFL','HULL','MUFL','MULL','LUFL','LULL']].to_numpy(), axis=0)
    #feats = ((feats - np.mean(feats, axis=0, keepdims=True)) / np.std(feats, axis=0, keepdims=True))
    #feats = np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 60
    #feats_discrete = np.abs((np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 60) // 15)
    feats_discrete = np.abs((np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 24*4))
    feats_discrete = np.expand_dims(feats_discrete, axis=-1)

    feats = np.concatenate([feats_discrete, feats_cont], axis=-1)

    #data = (data - np.mean(data, axis=0, keepdims=True)).T

    data = torch.tensor(data, dtype=torch.float)
    feats = torch.tensor(feats, dtype=torch.float)

    #import ipdb ; ipdb.set_trace()

    data_train = data[:, :train_len]
    feats_train = feats[:, :train_len]

    data_dev, data_test = [], []
    feats_dev, feats_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    for i in range(data.shape[0]):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= n:
                data_dev.append(data[i, :j])
                feats_dev.append(feats[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
    for i in range(data.shape[0]):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                feats_test.append(feats[i, :j])
                test_tsid_map[len(data_test)-1] = i


    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)


    decompose_type = 'STL'
    period=96
    for i in range(len(data_train)):
        data_train[i]['feats'] = feats_train[i]
        seq = data_train[i]['target']
        #data_train[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, True)
        data_train[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        print('train:', i, len(data_train))
    for i in range(len(data_dev)):
        data_dev[i]['feats'] = feats_dev[i]
        #import ipdb ; ipdb.set_trace()
        seq = data_dev[i]['target']
        #data_dev[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, False)
        data_dev[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        print('dev:', i, len(data_dev))
    for i in range(len(data_test)):
        data_test[i]['feats'] = feats_test[i]
        seq = data_test[i]['target']
        #data_test[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, False)
        data_test[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        print('test:', i, len(data_test))


    feats_info = {0:(24*4, 64), 1:(0, 1), 2:(0, 1), 3:(0, 1), 4:(0, 1), 5:(0, 1), 6:(0, 1)}
    coeffs_info = {0:(0, 1), 1:(0, 1), 2:(0, 1)}

    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )


def parse_sin_noisy(dataset_name, N_input, N_output):

    noise_len = 25

    X = np.arange(10000)

    y = np.sin(X * 2*np.pi/50.)
    noise_std = np.linspace(0, 1, noise_len)

    #for i in range(0, len(y), noise_len):
    #    y[i:i+noise_len] += np.random.normal(loc=np.zeros_like(noise_std), scale=noise_std)

    #data = torch.tensor(np.expand_dims(np.expand_dims(y, axis=-1), axis=0), dtype=torch.float)
    data = torch.tensor(np.expand_dims(y, axis=0), dtype=torch.float)
    n = data.shape[1]
    train_len = int(0.6*n)
    dev_len = int(0.2*n)
    test_len = n - train_len - dev_len

    #feats_cont = np.expand_dims(df[['HUFL','HULL','MUFL','MULL','LUFL','LULL']].to_numpy(), axis=0)
    #feats = ((feats - np.mean(feats, axis=0, keepdims=True)) / np.std(feats, axis=0, keepdims=True))
    #feats = np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 60
    feats_discrete = np.abs((np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 50))
    feats_discrete = np.expand_dims(feats_discrete, axis=-1)

    #feats = np.concatenate([feats_discrete, feats_cont], axis=-1)
    feats = feats_discrete

    feats = torch.tensor(feats, dtype=torch.float)

    #data = (data - np.mean(data, axis=0, keepdims=True)).T

    #data = torch.tensor(np.expand_dims(data, axis=-1), dtype=torch.float)
    #feats = torch.tensor(np.expand_dims(feats, axis=0), dtype=torch.float)

    data_train = data[:, :train_len]
    feats_train = feats[:, :train_len]

    data_dev, data_test = [], []
    feats_dev, feats_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    for i in range(data.shape[0]):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= n:
                data_dev.append(data[i, :j])
                feats_dev.append(feats[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
    for i in range(data.shape[0]):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                feats_test.append(feats[i, :j])
                test_tsid_map[len(data_test)-1] = i


    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    for i in range(len(data_train)):
        data_train[i]['feats'] = feats_train[i]
    for i in range(len(data_dev)):
        data_dev[i]['feats'] = feats_dev[i]
    for i in range(len(data_test)):
        data_test[i]['feats'] = feats_test[i]

    decompose_type = 'seasonal'
    period = 50
    for i in range(len(data_train)):
        data_train[i]['feats'] = feats_train[i]
        seq = data_train[i]['target']
        data_train[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, True)
        print('train:', i, len(data_train))
    for i in range(len(data_dev)):
        data_dev[i]['feats'] = feats_dev[i]
        #import ipdb ; ipdb.set_trace()
        seq = data_dev[i]['target']
        data_dev[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, False)
        print('dev:', i, len(data_dev))
    for i in range(len(data_test)):
        data_test[i]['feats'] = feats_test[i]
        seq = data_test[i]['target']
        data_test[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, False)
        print('test:', i, len(data_test))


    feats_info = {0:(50, 64)}
    coeffs_info = {0:(0, 1), 1:(0, 1), 2:(0, 1)}


    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )


def parse_Solar(dataset_name, N_input, N_output):
    
    data, feats = [], []
    with open(os.path.join(DATA_DIRS, 'data', 'solar_nips', 'train', 'train.json')) as f:
        for line in f:
            line_dict = json.loads(line)
            x = line_dict['target']
            data.append(x)
            x_f = (np.arange(len(x)) % 24)
            feats.append(x_f)

    data_test, feats_test = [], []
    with open(os.path.join(DATA_DIRS, 'data', 'solar_nips', 'test', 'test.json')) as f:
        for line in f:
            line_dict = json.loads(line)
            x = line_dict['target']
            x = np.array(x)
            #x = np.expand_dims(x, axis=-1)
            data_test.append(torch.tensor(x, dtype=torch.float))
            x_f = (np.cumsum(np.ones_like(x)) % 24)
            x_f = np.expand_dims(x_f, axis=-1)
            feats_test.append(torch.tensor(x_f, dtype=torch.float))

    data = np.array(data)
    data = torch.tensor(data, dtype=torch.float)

    # Features
    feats = torch.tensor(feats, dtype=torch.float).unsqueeze(dim=-1)
    #feats = np.sin(np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) * np.pi / 30.)
    #feats = np.expand_dims(feats, axis=-1)
    #feats = torch.tensor(feats, dtype=torch.int64)

    #data = torch.tensor(np.sin(feats.numpy()), dtype=torch.float)

    n = data.shape[1]
    #train_len = int(0.9*n)
    #dev_len = int(0.1*n)
    dev_len = 24*7
    train_len = n - dev_len
    #test_len = data_test.shape[1]

    data_train = data[:, :train_len]
    feats_train = feats[:, :train_len]

    data_dev = []
    feats_dev = []
    dev_tsid_map= {}
    for i in range(data.shape[0]):
        for j in range(train_len+N_output, n+1, N_output):
            if j <= n:
                data_dev.append(data[i, :j])
                feats_dev.append(feats[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
    #for i in range(len(data_test)):
    #    for j in range(n+N_output, n+1, N_output):
    #        if j <= len(data_test[i]):
    #            data_test.append(data_test[i, :j])
    #            feats_test.append(feats_test[i, :j])
    #            test_tsid_map[len(data_test)-1] = i % len(data)
    test_tsid_map = {}
    for i in range(len(data_test)):
        test_tsid_map[i] = i % len(data)

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    for i in range(len(data_train)):
        data_train[i]['feats'] = feats_train[i]
        data_train[i]['coeffs'] = torch.zeros((len(data_train[i]['target']), 1), dtype=torch.float)
    for i in range(len(data_dev)):
        data_dev[i]['feats'] = feats_dev[i]
        data_dev[i]['coeffs'] = torch.zeros((len(data_dev[i]['target']), 1), dtype=torch.float)
    for i in range(len(data_test)):
        data_test[i]['feats'] = feats_test[i]
        data_test[i]['coeffs'] = torch.zeros((len(data_test[i]['target']), 1), dtype=torch.float)

    feats_info = {0:(24, 16)}
    coeffs_info = {0:(0, 1)}

    #import ipdb;ipdb.set_trace()
                
    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )

def parse_etthourly(dataset_name, N_input, N_output):
    df = pd.read_csv('../Informer2020/data/ETT/ETTh1.csv')
    data = df[['OT']].to_numpy().T
    #data = np.expand_dims(data, axis=-1)

    n = data.shape[1]
    train_len = int(0.7*n)
    dev_len = int(0.1*n)
    test_len = n - train_len - dev_len

    feats_cont = np.expand_dims(df[['HUFL','HULL','MUFL','MULL','LUFL','LULL']].to_numpy(), axis=0)
    #feats = ((feats - np.mean(feats, axis=0, keepdims=True)) / np.std(feats, axis=0, keepdims=True))
    #feats = np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 60
    #feats_discrete = np.abs((np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 60) // 15)
    feats_discrete = np.abs((np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 24))
    feats_discrete = np.expand_dims(feats_discrete, axis=-1)

    feats = np.concatenate([feats_discrete, feats_cont], axis=-1)

    #data = (data - np.mean(data, axis=0, keepdims=True)).T

    data = torch.tensor(data, dtype=torch.float)
    feats = torch.tensor(feats, dtype=torch.float)

    data_train = data[:, :train_len]
    feats_train = feats[:, :train_len]

    data_dev, data_test = [], []
    feats_dev, feats_test = [], []
    dev_tsid_map, test_tsid_map = [], []
    for i in range(data.shape[0]):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= n:
                data_dev.append(data[i, :j])
                feats_dev.append(feats[i, :j])
                dev_tsid_map.append(i)
    for i in range(data.shape[0]):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                feats_test.append(feats[i, :j])
                test_tsid_map.append(i)


    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)


    decompose_type = 'STL'
    for i in range(len(data_train)):
        data_train[i]['feats'] = feats_train[i]
        seq = data_train[i]['target']
        #data_train[i]['coeffs'] = decompose_seq(seq, decompose_type, 24, N_output, True)
        data_train[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        #print('train:', i, len(data_train))
    for i in range(len(data_dev)):
        data_dev[i]['feats'] = feats_dev[i]
        #import ipdb ; ipdb.set_trace()
        seq = data_dev[i]['target']
        #data_dev[i]['coeffs'] = decompose_seq(seq, decompose_type, 24, N_output, False)
        data_dev[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        #print('dev:', i, len(data_dev))
    for i in range(len(data_test)):
        data_test[i]['feats'] = feats_test[i]
        seq = data_test[i]['target']
        #data_test[i]['coeffs'] = decompose_seq(seq, decompose_type, 24, N_output, False)
        data_test[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        #print('test:', i, len(data_test))

    feats_info = {0:(24, 16), 1:(0, 1), 2:(0, 1), 3:(0, 1), 4:(0, 1), 5:(0, 1), 6:(0, 1)}
    coeffs_info = {0:(0, 1), 1:(0, 1), 2:(0, 1)}

    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )


def parse_m4hourly(dataset_name, N_input, N_output):
    hourly_train = pd.read_csv(
        os.path.join(DATA_DIRS, '..', 'M4-methods', 'Dataset', 'Train', 'Hourly-train.csv'))
    hourly_test = pd.read_csv(
        os.path.join(DATA_DIRS, '..', 'M4-methods', 'Dataset', 'Test', 'Hourly-test.csv'))
    m4_info = pd.read_csv(os.path.join(DATA_DIRS, '..', 'M4-methods', 'Dataset', 'M4-info.csv'))

    lens = []
    ht_np = hourly_train.values[:, 1:]
    M, N = ht_np.shape
    for i in range(M):
        series = ht_np[i]
        l = N - np.isnan(series.astype(float)).sum()
        lens.append(l)

    hourly = []
    for (l, i, j) in zip(lens, hourly_train.values[:, 1:].astype(float), hourly_test.values[:, 1:].astype(float)):
        hourly.append(np.concatenate([i[:l], j]))

    hourly_train_merged = pd.merge(hourly_train, m4_info, left_on='V1', right_on='M4id', how='left')
    starting_dates = hourly_train_merged['StartingDate']
    starting_hours = pd.to_datetime(starting_dates).dt.hour.values
    hod = []
    for i, series in enumerate(hourly):
        hod_s = np.expand_dims((starting_hours[i] + np.arange(len(series))) % 24, axis=-1)
        hod.append(hod_s)
    #hod = (np.expand_dims(starting_hours, axis=1) + np.cumsum(np.ones(hourly_train.shape), axis=1) - 1.) % 24
    
    data = hourly
    feats = hod

    data_train, data_dev, data_test = [], [], []
    feats_train, feats_dev, feats_test = [], [], []
    dev_tsid_map, test_tsid_map = [], []

    dev_len = N_output
    test_len = N_output

    for i in range(len(data)):
        n = len(data[i])
        train_len = n - dev_len - test_len
        data_train.append(data[i][:train_len])
        feats_train.append(feats[i][:train_len])

        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= n:
                data_dev.append(data[i][:j])
                feats_dev.append(feats[i][:j])
                dev_tsid_map.append(i)

        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i][:j])
                feats_test.append(feats[i][:j])
                test_tsid_map.append(i)


    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)


    for i in range(len(data_train)):
        data_train[i]['target'] = torch.tensor(data_train[i]['target'])
        data_train[i]['feats'] = torch.tensor(feats_train[i])
        seq = data_train[i]['target']
        components = seasonal_decompose(
           seq, model='additive', period=24, extrapolate_trend=True
        )
        coeffs = torch.tensor(
            [components.trend, components.seasonal, components.resid]
        ).transpose(0,1)
        coeffs = (coeffs - coeffs.mean(dim=-1, keepdims=True)) / coeffs.std(dim=-1, keepdims=True)
        data_train[i]['coeffs'] = coeffs
    for i in range(len(data_dev)):
        data_dev[i]['target'] = torch.tensor(data_dev[i]['target'])
        data_dev[i]['feats'] = torch.tensor(feats_dev[i])
        seq = data_dev[i]['target']
        components = seasonal_decompose(
           seq, model='additive', period=24, extrapolate_trend=True
        )
        coeffs = torch.tensor(
            [components.trend, components.seasonal, components.resid]
        ).transpose(0,1)
        coeffs = (coeffs - coeffs.mean(dim=-1, keepdims=True)) / coeffs.std(dim=-1, keepdims=True)
        data_dev[i]['coeffs'] = coeffs
    for i in range(len(data_test)):
        data_test[i]['target'] = torch.tensor(data_test[i]['target'])
        data_test[i]['feats'] = torch.tensor(feats_test[i])
        seq = data_test[i]['target']
        components = seasonal_decompose(
           seq, model='additive', period=24, extrapolate_trend=True
        )
        coeffs = torch.tensor(
            [components.trend, components.seasonal, components.resid]
        ).transpose(0,1)
        coeffs = (coeffs - coeffs.mean(dim=-1, keepdims=True)) / coeffs.std(dim=-1, keepdims=True)
        data_test[i]['coeffs'] = coeffs

    feats_info = {0:(24, 16)}#, 1:(0, 1), 2:(0, 1), 3:(0, 1), 4:(0, 1), 5:(0, 1), 6:(0, 1)}
    coeffs_info = {0:(0, 1), 1:(0, 1), 2:(0, 1)}

    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )


def parse_m4daily(dataset_name, N_input, N_output):
    daily_train = pd.read_csv(
        os.path.join(DATA_DIRS, '..', 'M4-methods', 'Dataset', 'Train', 'Daily-train.csv'))
    daily_test = pd.read_csv(
        os.path.join(DATA_DIRS, '..', 'M4-methods', 'Dataset', 'Test', 'Daily-test.csv'))
    m4_info = pd.read_csv(os.path.join(DATA_DIRS, '..', 'M4-methods', 'Dataset', 'M4-info.csv'))

    daily_train_merged = pd.merge(daily_train, m4_info, left_on='V1', right_on='M4id', how='left')
    categories = daily_train_merged['category']
    indices = categories == 'Industry'
    #indices = categories == 'Macro'
    #indices = categories == 'Finance'
    daily_train = daily_train.loc[indices]
    daily_test = daily_test.loc[indices]

    lens = []
    dt_np = daily_train.values[:, 1:]
    M, N = dt_np.shape
    for i in range(M):
        series = dt_np[i]
        l = N - np.isnan(series.astype(float)).sum()
        lens.append(l)

    daily = []
    for (l, i, j) in zip(lens, daily_train.values[:, 1:].astype(float), daily_test.values[:, 1:].astype(float)):
        daily.append(np.concatenate([i[:l], j]))

    starting_dates = daily_train_merged['StartingDate']
    starting_doy = pd.to_datetime(starting_dates).dt.dayofweek
    doy = []
    for i, series in enumerate(daily):
        doy_s = np.expand_dims((starting_doy[i] + np.arange(len(series))) % 7, axis=-1)
        doy.append(doy_s)
    
    data = daily #[401:402]
    feats = doy #[401:402]

    data_train, data_dev, data_test = [], [], []
    feats_train, feats_dev, feats_test = [], [], []
    dev_tsid_map, test_tsid_map = [], []

    dev_len = N_output*3
    test_len = N_output

    for i in range(len(data)):
        n = len(data[i])
        train_len = n - dev_len - test_len
        data_train.append(data[i][:train_len])
        feats_train.append(feats[i][:train_len])

        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= n:
                data_dev.append(data[i][:j])
                feats_dev.append(feats[i][:j])
                dev_tsid_map.append(i)

        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i][:j])
                feats_test.append(feats[i][:j])
                test_tsid_map.append(i)


    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)


    decompose_type = 'STL'
    period = 90
    for i in range(len(data_train)):
        data_train[i]['target'] = torch.tensor(data_train[i]['target'])
        data_train[i]['feats'] = feats_train[i]
        seq = data_train[i]['target']
        data_train[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, True)
        print('train:', i, len(data_train))
    for i in range(len(data_dev)):
        data_dev[i]['target'] = torch.tensor(data_dev[i]['target'])
        data_dev[i]['feats'] = feats_dev[i]
        #import ipdb ; ipdb.set_trace()
        seq = data_dev[i]['target']
        data_dev[i]['coeffs'] = decompose_seq(seq, decompose_type, period, len(data_train[dev_tsid_map[i]]), False)
        print('dev:', i, len(data_dev))
    for i in range(len(data_test)):
        data_test[i]['target'] = torch.tensor(data_test[i]['target'])
        data_test[i]['feats'] = feats_test[i]
        seq = data_test[i]['target']
        data_test[i]['coeffs'] = decompose_seq(seq, decompose_type, period, len(data_train[test_tsid_map[i]]), False)
        print('test:', i, len(data_test))

    #import ipdb
    #ipdb.set_trace()

    feats_info = {0:(7, 16)}#, 1:(0, 1), 2:(0, 1), 3:(0, 1), 4:(0, 1), 5:(0, 1), 6:(0, 1)}
    coeffs_info = {0:(0, 1), 1:(0, 1), 2:(0, 1)}

    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )


def parse_taxi30min(dataset_name, N_input, N_output):

    num_rolling_windows = 1
    num_val_rolling_windows = 2
    dataset_dir = 'taxi_30min'

    data, feats = [], []
    with open(os.path.join(DATA_DIRS, 'data', dataset_dir, 'train', 'train.json')) as f:
        for line in f:
            line_dict = json.loads(line)
            x = line_dict['target']
            data.append(x)
            x_f = (np.arange(len(x)) % 48)
            feats.append(x_f)

    data_test, feats_test = [], []
    with open(os.path.join(DATA_DIRS, 'data', dataset_dir, 'test', 'test.json')) as f:
        for line in f:
            line_dict = json.loads(line)
            x = line_dict['target']
            x = np.array(x)
            data_test.append(torch.tensor(x, dtype=torch.float))
            x_f = (np.cumsum(np.ones_like(x)) % 48)
            x_f = np.expand_dims(x_f, axis=-1)
            feats_test.append(torch.tensor(x_f, dtype=torch.float))

    num_ts = 1214 * num_rolling_windows
    #import ipdb ; ipdb.set_trace()
    data_test = data_test[ -num_ts : ]
    feats_test = feats_test[ -num_ts : ]
    #for i in range(len(data_test)):
    #    assert data[i % len(data)]['lat'] == data_test[i]['lat']
    #    assert data[i % len(data)]['lng'] == data_test[i]['lng']

    metadata = json.load(open(os.path.join(DATA_DIRS, 'data', dataset_dir, 'metadata', 'metadata.json')))

    data = np.array(data)
    data = torch.tensor(data, dtype=torch.float)
    feats = torch.tensor(feats, dtype=torch.float).unsqueeze(dim=-1)

    n = data.shape[1]
    dev_len = N_output*num_val_rolling_windows
    train_len = n - dev_len
    data_train = data[:, :train_len]
    feats_train = feats[:, :train_len]

    data_dev, feats_dev = [], []
    dev_tsid_map = {}
    for i in range(data.shape[0]):
        for j in range(train_len+N_output, n+1, N_output):
            if j <= n:
                data_dev.append(data[i, :j])
                feats_dev.append(feats[i, :j])
                dev_tsid_map[len(data_dev)-1] = i

    test_tsid_map = {}
    for i, entry in enumerate(data_test, 0):
        test_tsid_map[i] = i%len(data) # Multiple test instances per train series.

    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)

    for i in range(len(data_train)):
        #data_train[i]['target'] = torch.tensor(data_train[i]['target'])
        data_train[i]['feats'] = feats_train[i]
        #data_train[i]['feats'] = torch.tensor(feats_train[i])
        seq = data_train[i]['target']
        #components = seasonal_decompose(
        #   seq, model='additive', period=48, extrapolate_trend=True
        #)
        #stl_components = STL(seq, period=48).fit()
        #stl_components_2 = STL(seq, period=336).fit()
        #coeffs = torch.tensor(
        #    [
        #        components.trend, components.seasonal, components.resid,
        #        #stl_components.trend, stl_components.seasonal, stl_components.resid,
        #        #stl_components_2.trend, stl_components_2.seasonal, stl_components_2.resid
        #    ]
        #).transpose(0,1)
        #coeffs = torch.log(coeffs)
        #coeffs = (coeffs - coeffs.mean(dim=-1, keepdims=True)) / coeffs.std(dim=-1, keepdims=True)
        #data_train[i]['coeffs'] = coeffs
        data_train[i]['coeffs'] = torch.zeros((len(data_train[i]['target']), 1), dtype=torch.float)
        #print(i)
    for i in range(len(data_dev)):
        #data_dev[i]['target'] = torch.tensor(data_dev[i]['target'])
        data_dev[i]['feats'] = feats_dev[i]
        #data_dev[i]['feats'] = torch.tensor(feats_dev[i])
        #import ipdb ; ipdb.set_trace()
        #seq_tr = data_dev[i]['target'][:-N_output]
        #seq_out = data_dev[i]['target'][-N_output:]
        #components_tr = seasonal_decompose(
        #   seq_tr, model='additive', period=48, extrapolate_trend=True
        #)
        #components_out = seasonal_decompose(
        #   seq_out, model='additive', period=3, extrapolate_trend=True
        #)
        ##stl_components_tr = STL(seq_tr, period=48).fit()
        ##stl_components_out = STL(seq_out, period=48).fit()
        ##stl_components_2_tr = STL(seq_tr, period=336).fit()
        ##stl_components_2_out = STL(seq_out, period=336).fit()
        #components_tr = torch.tensor(
        #    [
        #        components_tr.trend, components_tr.seasonal, components_tr.resid,
        #        #stl_components_tr.trend, stl_components_tr.seasonal, stl_components_tr.resid,
        #        #stl_components_2_tr.trend, stl_components_2_tr.seasonal, stl_components_2_tr.resid
        #    ]
        #).transpose(0,1)
        #components_out = torch.tensor(
        #    [
        #        components_out.trend, components_out.seasonal, components_out.resid,
        #        #stl_components_out.trend, stl_components_out.seasonal, stl_components_out.resid,
        #        #stl_components_2_out.trend, stl_components_2_out.seasonal, stl_components_2_out.resid
        #    ]
        #).transpose(0,1)

        ##coeffs = torch.log(coeffs)
        #means = components_tr.mean(dim=0, keepdims=True)
        #stds = components_tr.std(dim=0, keepdims=True)
        #components = torch.cat([components_tr, components_out], dim=0)
        #components = (components- means) / stds
        #data_dev[i]['coeffs'] = components
        data_dev[i]['coeffs'] = torch.zeros((len(data_dev[i]['target']), 1), dtype=torch.float)
    for i in range(len(data_test)):
        #data_test[i]['target'] = torch.tensor(data_test[i]['target'])
        data_test[i]['feats'] = feats_test[i]
        #data_test[i]['feats'] = torch.tensor(feats_test[i])
        #seq_tr = data_test[i]['target'][:-N_output]
        #seq_out = data_test[i]['target'][-N_output:]
        #components_tr = seasonal_decompose(
        #   seq_tr, model='additive', period=48, extrapolate_trend=True
        #)
        #components_out = seasonal_decompose(
        #   seq_out, model='additive', period=3, extrapolate_trend=True
        #) # TODO: this component is irrelevant
        ##stl_components_tr = STL(seq_tr, period=48).fit()
        ##stl_components_out = STL(seq_out, period=48).fit()
        ##stl_components_2_tr = STL(seq_tr, period=336).fit()
        ##stl_components_2_out = STL(seq_out, period=336).fit()
        #components_tr = torch.tensor(
        #    [
        #        components_tr.trend, components_tr.seasonal, components_tr.resid,
        #        #stl_components_tr.trend, stl_components_tr.seasonal, stl_components_tr.resid,
        #        #stl_components_2_tr.trend, stl_components_2_tr.seasonal, stl_components_2_tr.resid
        #    ]
        #).transpose(0,1)
        #components_out = torch.tensor(
        #    [
        #        components_out.trend, components_out.seasonal, components_out.resid,
        #        #stl_components_out.trend, stl_components_out.seasonal, stl_components_out.resid,
        #        #stl_components_2_out.trend, stl_components_2_out.seasonal, stl_components_2_out.resid
        #    ]
        #).transpose(0,1)

        ##coeffs = torch.log(coeffs)
        #means = components_tr.mean(dim=0, keepdims=True)
        #stds = components_tr.std(dim=0, keepdims=True)
        #components = torch.cat([components_tr, components_out], dim=0)
        #components = (components- means) / stds
        #data_test[i]['coeffs'] = components
        data_test[i]['coeffs'] = torch.zeros((len(data_test[i]['target']), 1), dtype=torch.float)

    feats_info = {0:(48, 8)}
    #coeffs_info = {0:(0, 1), 1:(0, 1), 2:(0, 1)}
    coeffs_info = {0:(0, 1)}

    #import ipdb ; ipdb.set_trace()
        
    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map, feats_info, coeffs_info
    )


def parse_Traffic911(N_input, N_output):
    call_df = pd.read_csv(os.path.join(DATA_DIRS, 'data', '911.csv'))
    call_df = call_df[call_df['zip'].isnull()==False] # Ignore calls with NaN zip codes
#     print('Types of Emergencies')
#     print(call_df.title.apply(lambda x: x.split(':')[0]).value_counts())
    call_df['type'] = call_df.title.apply(lambda x: x.split(':')[0])
#     print('Subtypes')
#     for each in call_df.type.unique():
#         subtype_count = call_df[call_df.title.apply(lambda x: x.split(':')[0]==each)].title.value_counts()
#         print('For', each, 'type of Emergency, we have ', subtype_count.count(), 'subtypes')
#         print(subtype_count[subtype_count>100])
#     print('Out of 3 types, considering only Traffic')
    call_data = call_df[call_df['type']=='Traffic']
    call_data['timeStamp'] = pd.to_datetime(call_data['timeStamp'], errors='coerce')
    print("We have timeline from", call_data['timeStamp'].min(), "to", call_data['timeStamp'].max())
    call_data = call_data.sort_values('timeStamp')
    call_data['timeStamp'] = pd.DatetimeIndex(call_data['timeStamp']).astype(np.int64)/1000000000

    num_hrs = int(
        np.ceil(
            (call_data['timeStamp'].values[-1] - call_data['timeStamp'].values[0])/(3600.)
        )
    )
    timestamps = call_data['timeStamp'].values
    timestamps = timestamps - timestamps[0]
    counts = create_bins(timestamps, bin_size=3600., num_bins=num_hrs)
    data = np.expand_dims(np.array(counts), axis=0)
    #data = np.expand_dims(data, axis=2)
    data = torch.tensor(data, dtype=torch.float)

    n = data.shape[1]
    train_len = int(0.7*n)
    dev_len = int(0.1*n)
    test_len = n - train_len - dev_len

    feats = np.abs((np.ones((data.shape[0], 1)) * np.expand_dims(np.arange(n), axis=0) % 24))
    feats = np.expand_dims(feats, axis=-1)

    feats = torch.tensor(feats, dtype=torch.float)

    data_train = data[:, :train_len]
    feats_train = feats[:, :train_len]

    data_dev, data_test = [], []
    feats_dev, feats_test = [], []
    dev_tsid_map, test_tsid_map = {}, {}
    for i in range(data.shape[0]):
        for j in range(train_len+N_output, train_len+dev_len+1, N_output):
            if j <= n:
                data_dev.append(data[i, :j])
                feats_dev.append(feats[i, :j])
                dev_tsid_map[len(data_dev)-1] = i
    for i in range(data.shape[0]):
        for j in range(train_len+dev_len+N_output, n+1, N_output):
            if j <= n:
                data_test.append(data[i, :j])
                feats_test.append(feats[i, :j])
                test_tsid_map[len(data_test)-1] = i


    data_train = get_list_of_dict_format(data_train)
    data_dev = get_list_of_dict_format(data_dev)
    data_test = get_list_of_dict_format(data_test)


    decompose_type = 'STL'
    period=96
    for i in range(len(data_train)):
        data_train[i]['feats'] = feats_train[i]
        seq = data_train[i]['target']
        #data_train[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, True)
        data_train[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        #print('train:', i, len(data_train))
    for i in range(len(data_dev)):
        data_dev[i]['feats'] = feats_dev[i]
        #import ipdb ; ipdb.set_trace()
        seq = data_dev[i]['target']
        #data_dev[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, False)
        data_dev[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        #print('dev:', i, len(data_dev))
    for i in range(len(data_test)):
        data_test[i]['feats'] = feats_test[i]
        seq = data_test[i]['target']
        #data_test[i]['coeffs'] = decompose_seq(seq, decompose_type, period, N_output, False)
        data_test[i]['coeffs'] = torch.zeros((len(seq), 1), dtype=torch.float)
        #print('test:', i, len(data_test))


    feats_info = {0:(24, 16)}
    coeffs_info = {0:(0, 1)}

    return (
        data_train, data_dev, data_test, dev_tsid_map, test_tsid_map,
        feats_info, coeffs_info
    )
