# .trace is the scarlet format (more "readable")
# .dat is the stlnet format (actually used by the code)

# scarlet format is for readability only and NOT an accurate implementation of the format.
# for example, it's missing the positive-negative divide,
# as well as the conclusion with the operator and symbol lists.

from pathlib import Path
import xml.etree.ElementTree as ET
import pm4py
import json

from generate_alphabet import get_ct_symbol_name
import convert_traces_alignment_module as ctam

def trace_names_are_unique(fpath):
    tree = ET.parse(fpath)
    root = tree.getroot()
    names = root.findall("./trace/string[@key='concept:name']")
    names = [e.attrib["value"] for e in names]
    # don't think we can avoid this O(N^2)
    for i, name1 in enumerate(names):
        for name2 in names[:i]:
            if name1 == name2:
                return False
    return True

def symbol_to_scarlet(symbol, alphabet):
    onehot = ["1" if symbol==s else "0" for s in alphabet]
    return ",".join(onehot)

def xes_to_scarlet(include_transitions):
    return _convertxes(include_transitions, do_scarlet=True, do_stlnet=False)

def xes_to_stlnet(include_transitions):
    return _convertxes(include_transitions, do_scarlet=False, do_stlnet=True)

def _convertxes(include_transitions, *, do_scarlet, do_stlnet, do_align):
    for filename in Path(__file__).parent.iterdir():
        if filename.suffix == ".xes":
            ### HANDLE FILE
            assert trace_names_are_unique(filename)
            if include_transitions:
                alphabet_fname = "ALPHABET_ct.LIST"
                symbolize = get_ct_symbol_name
            else:
                alphabet_fname = "ALPHABET_c.LIST"
                symbolize = lambda s:s
            with open(Path(__file__).parent / alphabet_fname, "r") as f:
                alphabet = json.load(f)
            aligner = None
            if do_align and filename.with_suffix(".plans.txt").exists():
                aligner = ctam.Aligner(filename.with_suffix(".plans.txt"), alphabet)
                print(filename.with_suffix(".plans.txt"), "found.")
            log = pm4py.read_xes(str(filename))
            trace_names = log["case:concept:name"].unique()
            include_transitions = include_transitions and "lifecycle:transition" in log.columns
            traces_scarlet = []
            lengths = []
            for tn in trace_names:
                ### HANDLE TRACE
                trace_df = log[log["case:concept:name"] == tn]
                if include_transitions:
                    trace_df = trace_df[["concept:name", "lifecycle:transition"]]
                else:
                    trace_df = trace_df["concept:name"]
                trace_list = [symbolize(row) for row in trace_df.values]
                if aligner is not None:
                    trace_list = aligner.align(trace_list, len(traces_scarlet))
                trace_halfscarlet = [symbol_to_scarlet(s, alphabet) for s in trace_list]
                traces_scarlet.append(";".join(trace_halfscarlet))
                lengths.append(str(len(trace_halfscarlet))+"\n")
            # HANDLE FILE cont.
            superstring_scarlet = "\n".join(traces_scarlet)
            if (do_scarlet):
                with open(Path(__file__).parent / "scarlet" / filename.with_suffix(".trace").name, "w") as f:
                    f.write(superstring_scarlet)
            if (do_stlnet):
                superstring_stlnet = superstring_scarlet.replace(",", " ").replace(";", " ")
                with open(Path(__file__).parent / "stlnet" / filename.with_suffix(".dat").name, "w") as f:
                    f.write(superstring_stlnet)
            with open(Path(__file__).parent / "lengths" / filename.with_suffix(".txt").name, "w") as f:
                f.writelines(lengths)

def megamerge(directory, suffix):
    first_write = True
    with open(directory / ("50-75-100-128-merge"+suffix), "w") as fout:
        for filename in directory.iterdir():
            if filename.suffix == suffix and filename.stem in ("50","75","100","128"):
                with open(filename, "r") as fin:
                    if not first_write:
                        fout.write("\n")
                    fout.write(fin.read())
                    first_write = False

if __name__ == "__main__":
    _convertxes(True, do_scarlet=True, do_stlnet=True, do_align=True)
    megamerge(Path(__file__).parent / "scarlet", ".trace")
    megamerge(Path(__file__).parent / "stlnet", ".dat")
