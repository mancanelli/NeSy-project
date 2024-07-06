import pathlib
import time
import json
import numpy as np
import torch

from statistics import mean
from copy import deepcopy
import xml.etree.ElementTree as ET

from LTL2STL import infix_to_prefix, prefix_LTL_to_scarlet2
from RNN import LSTM_model
from training import train
from evaluation import suffix_prediction_with_temperature_with_stop, evaluate_compliance_with_formula, evaluate_DL_distance, greedy_suffix_prediction_with_stop
from FiniteStateMachine import DFA
from utils import expand_dataset_with_end_of_trace_symbol
from sample_interesting_formulas import list_to_one_hot

if torch.cuda.is_available():
    device = "cuda:0"
else:
    device = "cpu"

def filter_traces_by_length(log_file, out_file, length):
    tree = ET.parse(log_file)
    root = tree.getroot()

    selected_traces = []

    for trace in root.iter('trace'):
        events = trace.findall('event')
        
        if len(events) == length:
            selected_traces.append(trace)

    new_root = ET.Element(root.tag, root.attrib)

    for trace in selected_traces:
        new_root.append(trace)

    new_tree = ET.ElementTree(new_root)
    new_tree.write(out_file, encoding='UTF-8', xml_declaration=True)

    return len(selected_traces)

def extractSymbols(log_file, traces_file):
    tree = ET.parse(log_file)
    root = tree.getroot()

    alphabet = set()
    #n_traces_per_length = {}

    with open(traces_file, "w") as f:
        for trace in root.iter('trace'):
            #n_events = 0
            sym_list = []

            for event in trace.iter('event'):
                #n_events += 1

                for string in event.findall("string"):
                    if string.attrib.get('key') == 'lifecycle:transition':
                        transaction = string.attrib.get('value').lower()
                    if string.attrib.get('key') == 'concept:name':
                        concept = string.attrib.get('value').lower().replace(" ", "_")
                    
                symbol = concept + "_" + transaction
                sym_list.append(symbol)
                alphabet.add(symbol)
            
            f.write(f"{sym_list}\n")
            #n_traces_per_length.update({n_events: n_traces_per_length.setdefault(n_events, 0) + 1})
    
    return sorted(alphabet)

def traces_to_scarlet(traces_file, scarlet_file, alphabet):
    with open(traces_file, "r") as input:
        with open(scarlet_file, "w") as output:
            for line in input:
                trace = eval(line.rstrip("\n"))
                one_hot = list_to_one_hot(trace, alphabet)
                output.write(";".join([",".join(map(lambda x: str(int(x)), seq)) for seq in one_hot]) + "\n")

def traces_to_stlnet(traces_file, stlnet_file, alphabet):
    with open(traces_file, "r") as input:
        with open(stlnet_file, "w") as output:
            for line in input:
                trace = eval(line.rstrip("\n"))
                one_hot = list_to_one_hot(trace, alphabet)
                output.write(" ".join([" ".join(map(lambda x: str(int(x)), seq)) for seq in one_hot]) + "\n")

def getMutex(alphabet):
    mutex_str = " & ".join(["(" + symbol + " i (" + " & ".join(["! " + sym for sym in alphabet if sym != symbol]) + "))" for symbol in alphabet])
    mutex_str = mutex_str + " & (" + " | ".join([symbol for symbol in alphabet]) + ")"
    return "(G(" + mutex_str + "))"

def main():
    experiment_datetime = time.strftime("%Y-%m-%d_%H-%M-%S")
    results_folder = pathlib.Path("results", f"results_{experiment_datetime}")
    results_file = pathlib.Path(results_folder, "results.txt")
    results_folder.mkdir(parents=True, exist_ok=True)
    results_file.touch()

    # Number of experiments
    N_FORMULAS = 5
    N_EXPERIMENTS_PER_FORMULA = 5

    # Parameters for RNN
    HIDDEN_DIM = 100
    TRAIN_RATIO = 0.9
    TEMPERATURE = 0.7
    MAX_NUM_EPOCHS = 4000
    EPSILON = 0.01

    # PARAMETERS TO VARY
    TRACE_LENGTH = 20
    PREFIX_LEN_START_VALUE = 5
    PREFIX_LEN_INCREMENT = 5
    PREFIX_LEN_INCREMENT_ITERATIONS = 3

    # Dictionary to store the results for each configuration
    configuration_results = {}

    # Track execution time per config
    start_time = time.time()

    # Variables to store the results of the current configuration
    configuration_results[str("real")] = {}

    # Create dataset folders and files
    data_folder = pathlib.Path("datasets", "real")
    data_folder.mkdir(parents=True, exist_ok=True)

    file_name = "dutch_financial_log"
    filtered_file_name = f"l{TRACE_LENGTH}"

    data_file = pathlib.Path(data_folder, f"{filtered_file_name}.xes")

    if filtered_file_name:
        filtered_file = pathlib.Path(data_folder, f"{filtered_file_name}.xes")
        filter_traces_by_length(data_file, filtered_file, TRACE_LENGTH)
    else:
        filtered_file_name = file_name
        filtered_file = data_file

    traces_file = pathlib.Path(data_folder, f"{filtered_file_name}.txt")
    scarlet_file = pathlib.Path(data_folder, f"{filtered_file_name}_scarlet.traces")
    stlnet_file = pathlib.Path(data_folder, f"{filtered_file_name}_stlnet.dat")

    alphabet = extractSymbols(filtered_file, traces_file)
    NVAR = len(alphabet)

    traces_to_scarlet(traces_file, scarlet_file, alphabet)
    traces_to_stlnet(traces_file, stlnet_file, alphabet)
    
    stop_event = [0] * len(alphabet)
    stop_event.append(1)
    alphabet.append("end")
    
    # Formulas
    formula_folder = pathlib.Path("formulas", "real")
    formula_folder.mkdir(parents=True, exist_ok=True)

    formula_file = pathlib.Path(formula_folder, "formulas.txt")
    formula_scarlet_file = pathlib.Path(formula_folder, "formula_scarlet.txt")

    with open(formula_file, "r") as f:
        formulas_infix = []
        formula_scarlet_lst = []

        for line in f.readlines():
            formulas_infix.append(line.rstrip('\n').replace(" i ", " -> ").replace(" e ", " <-> "))

            formula = "(" + line.rstrip('\n') + ") & " + getMutex(alphabet) # Declare assumption
            #print(formula)
            formula_prefix = infix_to_prefix(formula)
            #print("in prefix format:", formula_prefix)
            formula_scarlet, _ = prefix_LTL_to_scarlet2(formula_prefix.split(" "))
            #print("in scarlet format:", formula_scarlet)
            formula_scarlet_lst.append(formula_scarlet)
    
    with open(formula_scarlet_file, "w") as f:
        for formula in formula_scarlet_lst:
            f.write(f"{formula};" + ','.join([symbol for symbol in alphabet])  + "\n")

    # Run experiments for each formula
    for i_form, formula in enumerate(formulas_infix):
        configuration_results[str("real")][i_form] = {}
        configuration_results[str("real")][i_form]["results"] = {}

        # DFA formula evaluator
        dfa = DFA(formula, NVAR, "declare", alphabet)
        deep_dfa = dfa.return_deep_dfa()

        # Dataset
        dataset = torch.tensor(np.loadtxt(stlnet_file))
        dataset = dataset.view(dataset.size(0), -1, NVAR)
        dataset = expand_dataset_with_end_of_trace_symbol(dataset)
        dataset = dataset.float()
        num_traces = dataset.size()[0]

        # Splitting in train and test
        train_dataset = dataset[: int(TRAIN_RATIO * num_traces)]
        test_dataset = dataset[int(TRAIN_RATIO * num_traces) :]

        # Variables to store the results of each experiment of the current formula, and for each prefix length value
        formula_experiment_results = {}
        for current_prefix_len in range(PREFIX_LEN_START_VALUE, PREFIX_LEN_START_VALUE + PREFIX_LEN_INCREMENT * PREFIX_LEN_INCREMENT_ITERATIONS, PREFIX_LEN_INCREMENT):
            formula_experiment_results[current_prefix_len] = {
                # RNN results
                "train_acc_rnn": [],
                "test_acc_rnn": [],
                "train_DL_rnn": [],
                "test_DL_rnn": [],
                "train_sat_rnn": [],
                "test_sat_rnn": [],
                # RNN+BK results
                "train_acc_rnn_bk": [],
                "test_acc_rnn_bk": [],
                "train_DL_rnn_bk": [],
                "test_DL_rnn_bk": [],
                "train_sat_rnn_bk": [],
                "test_sat_rnn_bk": [],
                # RNN Greedy results
                "train_acc_rnn_greedy": [],
                "test_acc_rnn_greedy": [],
                "train_DL_rnn_greedy": [],
                "test_DL_rnn_greedy": [],
                "train_sat_rnn_greedy": [],
                "test_sat_rnn_greedy": [],
                # RNN+BK Greedy results
                "train_acc_rnn_bk_greedy": [],
                "test_acc_rnn_bk_greedy": [],
                "train_DL_rnn_bk_greedy": [],
                "test_DL_rnn_bk_greedy": [],
                "train_sat_rnn_bk_greedy": [],
                "test_sat_rnn_bk_greedy": []
            }

        # Run N_EXPERIMENTS_PER_FORMULA experiments for each formula
        for exp in range(N_EXPERIMENTS_PER_FORMULA):
            # Models
            rnn = LSTM_model(HIDDEN_DIM, NVAR + 1, NVAR + 1)
            rnn_bk = deepcopy(rnn)

            ########################################################
            # Experiment with RNN and RNN Greedy
            ########################################################

            # Instantiate model
            model = deepcopy(rnn).to(device)

            # Training
            train_acc, test_acc = train(model, train_dataset, test_dataset, MAX_NUM_EPOCHS, EPSILON)
            
            # Save the model
            model_file = pathlib.Path(results_folder, f"model_rnn_formula_{i_form}_exp_{exp}.pt")
            torch.save(model.state_dict(), model_file)

            # We save the results for all prefix length values cause the training is the same for each value
            for current_prefix_len in range(PREFIX_LEN_START_VALUE, PREFIX_LEN_START_VALUE + PREFIX_LEN_INCREMENT * PREFIX_LEN_INCREMENT_ITERATIONS, PREFIX_LEN_INCREMENT):
                formula_experiment_results[current_prefix_len]["train_acc_rnn"].append(train_acc)
                formula_experiment_results[current_prefix_len]["test_acc_rnn"].append(test_acc)
                formula_experiment_results[current_prefix_len]["train_acc_rnn_greedy"].append(train_acc)
                formula_experiment_results[current_prefix_len]["test_acc_rnn_greedy"].append(test_acc)

            # RNN Suffix prediction with temperature
            for current_prefix_len in range(PREFIX_LEN_START_VALUE, PREFIX_LEN_START_VALUE + PREFIX_LEN_INCREMENT * PREFIX_LEN_INCREMENT_ITERATIONS, PREFIX_LEN_INCREMENT):
                train_predicted_traces = suffix_prediction_with_temperature_with_stop(model, train_dataset, current_prefix_len, stop_event=stop_event, temperature=TEMPERATURE)
                test_predicted_traces = suffix_prediction_with_temperature_with_stop(model, test_dataset, current_prefix_len, stop_event=stop_event, temperature=TEMPERATURE)

                # Evaluating compliance with the formula of stochastic sampling
                train_sat = evaluate_compliance_with_formula(deep_dfa, train_predicted_traces)
                test_sat = evaluate_compliance_with_formula(deep_dfa, test_predicted_traces)
                formula_experiment_results[current_prefix_len]["train_sat_rnn"].append(train_sat)
                formula_experiment_results[current_prefix_len]["test_sat_rnn"].append(test_sat)

                # Evaluating DL distance
                train_DL = evaluate_DL_distance(train_predicted_traces, train_dataset)
                test_DL = evaluate_DL_distance(test_predicted_traces, test_dataset)
                formula_experiment_results[current_prefix_len]["train_DL_rnn"].append(train_DL)
                formula_experiment_results[current_prefix_len]["test_DL_rnn"].append(test_DL)

                print(f"____________________RNN TEMPERATURE PREDICTION formula {i_form} / experiment {exp} / prefix length {current_prefix_len}____________________")
                print(f"Satisfaction of formula {i_form}:")
                print("- Train: ", train_sat)
                print("- Test: ", test_sat)
                print("DL distance:")
                print("- Train: ", train_DL)
                print("- Test: ", test_DL)

            # RNN greedy suffix prediction
            for current_prefix_len in range(PREFIX_LEN_START_VALUE, PREFIX_LEN_START_VALUE + PREFIX_LEN_INCREMENT * PREFIX_LEN_INCREMENT_ITERATIONS, PREFIX_LEN_INCREMENT):
                train_predicted_traces = greedy_suffix_prediction_with_stop(model, train_dataset, current_prefix_len, stop_event=stop_event)
                test_predicted_traces = greedy_suffix_prediction_with_stop(model, test_dataset, current_prefix_len, stop_event=stop_event)

                # Evaluating compliance with the formula of stochastic sampling
                train_sat = evaluate_compliance_with_formula(deep_dfa, train_predicted_traces)
                test_sat = evaluate_compliance_with_formula(deep_dfa, test_predicted_traces)
                formula_experiment_results[current_prefix_len]["train_sat_rnn_greedy"].append(train_sat)
                formula_experiment_results[current_prefix_len]["test_sat_rnn_greedy"].append(test_sat)

                # Evaluating DL distance
                train_DL = evaluate_DL_distance(train_predicted_traces, train_dataset)
                test_DL = evaluate_DL_distance(test_predicted_traces, test_dataset)
                formula_experiment_results[current_prefix_len]["train_DL_rnn_greedy"].append(train_DL)
                formula_experiment_results[current_prefix_len]["test_DL_rnn_greedy"].append(test_DL)

                print(f"____________________RNN GREEDY PREDICTION formula {i_form} / experiment {exp} / prefix length {current_prefix_len}____________________")
                print(f"Satisfaction of formula {i_form}:")
                print("- Train: ", train_sat)
                print("- Test: ", test_sat)
                print("DL distance:")
                print("- Train: ", train_DL)
                print("- Test: ", test_DL)

            ########################################################
            # Experiment RNN+BK
            ########################################################
            
            for current_prefix_len in range(PREFIX_LEN_START_VALUE, PREFIX_LEN_START_VALUE + PREFIX_LEN_INCREMENT * PREFIX_LEN_INCREMENT_ITERATIONS, PREFIX_LEN_INCREMENT):
                # Instantiate model
                model = deepcopy(rnn_bk).to(device)
                
                # Training
                train_acc, test_acc = train(model, train_dataset, test_dataset, MAX_NUM_EPOCHS, EPSILON, deepdfa=deep_dfa, prefix_len=current_prefix_len)
                
                # Save the model
                model_file = pathlib.Path(results_folder, f"model_rnn_bk_formula_{i_form}_exp_{exp}_prefix_len_{current_prefix_len}.pt")
                torch.save(model.state_dict(), model_file)

                # Save the results for all prefix length values cause the training is the same for each value
                formula_experiment_results[current_prefix_len]["train_acc_rnn_bk"].append(train_acc)
                formula_experiment_results[current_prefix_len]["test_acc_rnn_bk"].append(test_acc)
                formula_experiment_results[current_prefix_len]["train_acc_rnn_bk_greedy"].append(train_acc)
                formula_experiment_results[current_prefix_len]["test_acc_rnn_bk_greedy"].append(test_acc)

                # Suffix prediction with temperature
                train_predicted_traces = suffix_prediction_with_temperature_with_stop(model, train_dataset, current_prefix_len, stop_event=stop_event, temperature=TEMPERATURE)
                test_predicted_traces = suffix_prediction_with_temperature_with_stop(model, test_dataset, current_prefix_len, stop_event=stop_event, temperature=TEMPERATURE)

                # Evaluating compliance with the formula of stochastic sampling
                train_sat = evaluate_compliance_with_formula(deep_dfa, train_predicted_traces)
                test_sat = evaluate_compliance_with_formula(deep_dfa, test_predicted_traces)
                formula_experiment_results[current_prefix_len]["train_sat_rnn_bk"].append(train_sat)
                formula_experiment_results[current_prefix_len]["test_sat_rnn_bk"].append(test_sat)

                # Evaluating DL distance
                train_DL = evaluate_DL_distance(train_predicted_traces, train_dataset)
                test_DL = evaluate_DL_distance(test_predicted_traces, test_dataset)
                formula_experiment_results[current_prefix_len]["train_DL_rnn_bk"].append(train_DL)
                formula_experiment_results[current_prefix_len]["test_DL_rnn_bk"].append(test_DL)

                print(f"____________________RNN+BK TEMPERATURE PREDICTION formula {i_form} / experiment {exp} / prefix length {current_prefix_len}____________________")
                print("Satisfaction:")
                print("- Train: ", train_sat)
                print("- Test: ", test_sat)
                print("DL distance:")
                print("- Train: ", train_DL)
                print("- Test: ", test_DL)

                # Greedy suffix prediction
                train_predicted_traces = greedy_suffix_prediction_with_stop(model, train_dataset, current_prefix_len, stop_event=stop_event)
                test_predicted_traces = greedy_suffix_prediction_with_stop(model, test_dataset, current_prefix_len, stop_event=stop_event)

                # Evaluating compliance with the formula of stochastic sampling
                train_sat = evaluate_compliance_with_formula(deep_dfa, train_predicted_traces)
                test_sat = evaluate_compliance_with_formula(deep_dfa, test_predicted_traces)
                formula_experiment_results[current_prefix_len]["train_sat_rnn_bk_greedy"].append(train_sat)
                formula_experiment_results[current_prefix_len]["test_sat_rnn_bk_greedy"].append(test_sat)

                # Evaluating DL distance
                train_DL = evaluate_DL_distance(train_predicted_traces, train_dataset)
                test_DL = evaluate_DL_distance(test_predicted_traces, test_dataset)
                formula_experiment_results[current_prefix_len]["train_DL_rnn_bk_greedy"].append(train_DL)
                formula_experiment_results[current_prefix_len]["test_DL_rnn_bk_greedy"].append(test_DL)

                print(f"____________________RNN+BK GREEDY PREDICTION formula {i_form} / experiment {exp} / prefix length {current_prefix_len}____________________")
                print("Satisfaction:")
                print("- Train: ", train_sat)
                print("- Test: ", test_sat)
                print("DL distance:")
                print("- Train: ", train_DL)
                print("- Test: ", test_DL)

            # Track time of execution
            end_time = time.time()
            print(f"Execution time for experiment {exp}: ", end_time - start_time)

            # Save the results of the experiment number {exp} for the current formula
            configuration_results[str("real")][i_form]["results"] = formula_experiment_results
            # Save in text file
            results_config_file = pathlib.Path(results_folder, "results.txt")
            with open(results_config_file, "a") as f:
                f.write(f"____________{i_form=}___{exp=}____________\n")
                for current_prefix_len in range(PREFIX_LEN_START_VALUE, PREFIX_LEN_START_VALUE + PREFIX_LEN_INCREMENT * PREFIX_LEN_INCREMENT_ITERATIONS, PREFIX_LEN_INCREMENT):
                    f.write(f"- Prefix length: {current_prefix_len}\n")
                    f.write("train acc next activity:\nRNN:{}\tRNN+BK:{}\tRNN Greedy:{}\tRNN+BK Greedy:{}\n".format(mean(formula_experiment_results[current_prefix_len]["train_acc_rnn"]), mean(formula_experiment_results[current_prefix_len]["train_acc_rnn_bk"]), mean(formula_experiment_results[current_prefix_len]["train_acc_rnn_greedy"]), mean(formula_experiment_results[current_prefix_len]["train_acc_rnn_bk_greedy"])))
                    f.write("test acc next activity:\nRNN:{}\tRNN+BK:{}\tRNN Greedy:{}\tRNN+BK Greedy:{}\n\n".format(mean(formula_experiment_results[current_prefix_len]["test_acc_rnn"]), mean(formula_experiment_results[current_prefix_len]["test_acc_rnn_bk"]), mean(formula_experiment_results[current_prefix_len]["test_acc_rnn_greedy"]), mean(formula_experiment_results[current_prefix_len]["test_acc_rnn_bk_greedy"])))
                    f.write("train DL distance:\nRNN:{}\tRNN+BK:{}\tRNN Greedy:{}\tRNN+BK Greedy:{}\n".format(mean(formula_experiment_results[current_prefix_len]["train_DL_rnn"]), mean(formula_experiment_results[current_prefix_len]["train_DL_rnn_bk"]), mean(formula_experiment_results[current_prefix_len]["train_DL_rnn_greedy"]), mean(formula_experiment_results[current_prefix_len]["train_DL_rnn_bk_greedy"])))
                    f.write("test DL distance:\nRNN:{}\tRNN+BK:{}\tRNN Greedy:{}\tRNN+BK Greedy:{}\n\n".format(mean(formula_experiment_results[current_prefix_len]["test_DL_rnn"]), mean(formula_experiment_results[current_prefix_len]["test_DL_rnn_bk"]), mean(formula_experiment_results[current_prefix_len]["test_DL_rnn_greedy"]), mean(formula_experiment_results[current_prefix_len]["test_DL_rnn_bk_greedy"])))
                    f.write("train sat suffix:\nRNN:{}\tRNN+BK:{}\tRNN Greedy:{}\tRNN+BK Greedy:{}\n".format(mean(formula_experiment_results[current_prefix_len]["train_sat_rnn"]), mean(formula_experiment_results[current_prefix_len]["train_sat_rnn_bk"]), mean(formula_experiment_results[current_prefix_len]["train_sat_rnn_greedy"]), mean(formula_experiment_results[current_prefix_len]["train_sat_rnn_bk_greedy"])))
                    f.write("test sat suffix:\nRNN:{}\tRNN+BK:{}\tRNN Greedy:{}\tRNN+BK Greedy:{}\n".format(mean(formula_experiment_results[current_prefix_len]["test_sat_rnn"]), mean(formula_experiment_results[current_prefix_len]["test_sat_rnn_bk"]), mean(formula_experiment_results[current_prefix_len]["test_sat_rnn_greedy"]), mean(formula_experiment_results[current_prefix_len]["test_sat_rnn_bk_greedy"])))
                    f.write("\n")
                f.write("Execution time: {}\n\n".format(end_time - start_time))
            # Save in JSON file
            results_config_json_file = pathlib.Path(results_folder, "results.json")
            with open(results_config_json_file, "w+") as f:
                json.dump(configuration_results, f, indent=4)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Save the exception to the results file
        with open("results/exceptions.txt", "a") as f:
            f.write(f"{str(e)} \n\n")
        raise e