import argparse
from cooperace import Cooperace

parser = argparse.ArgumentParser(description="A simple script to demonstrate CLI arguments in Python")

parser.add_argument('--properties_file', required=False)
parser.add_argument('--filepath', required=False)

args = parser.parse_args()

cooperace = Cooperace(args.filepath, args.properties_file)
    
print(cooperace.start())
