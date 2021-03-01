#! /misc/local/python-3.8.2/bin/python3
"""
llsm-pipeline
This program is the entry point for the LLSM pipeline.
"""

import argparse
import os
import re
import json
from pathlib import Path, PurePath
from sys import exit
import settings2json

def parse_args():
    parser = argparse.ArgumentParser(description='Batch deskewing and deconvolution script for LLSM images.')
    parser.add_argument('input', type=Path, help='path to configuration JSON file')
    parser.add_argument('--dry-run', '-d', default=False, action='store_true', dest='dryrun',help='execute without submitting any bsub jobs')
    parser.add_argument('--verbose', '-v', default=False, action='store_true', dest='verbose',help='print details (including commands to bsub)')
    args = parser.parse_args()

    print(args.dryrun)
    if not args.input.is_file():
        exit(f'error: \'%s\' does not exist' % args.input)

    if not args.input.suffix == '.json':
        print(f'warning: \'%s\' does not appear to be a settings file\n' % args.input)

    return args

def load_configs(path):
    with path.open(mode='r') as f:
        try:
            configs = json.load(f)
        except json.JSONDecodeError as e:
            exit(f'error: \'%s\' is not formatted as a proper JSON file...\n%s' % (path, e))

    # check critical configs
    root = Path(configs["paths"]["root"])
    if not root.is_dir():
        exit(f'error: root path \'%s\' does not exist' % root)

    if 'decon' in configs:
        if 'psf' not in configs['paths']:
            exit(f'error: decon enabled, but no psf parameters found in config file')

        if 'dir' in configs['paths']['psf']:
            partial_path = root / configs['paths']['psf']['dir']
        else:
            configs['paths']['psf']['dir'] = None
            partial_path = root
            print(f'warning: no psf directory provided... using \'%s\'' % root)
        
        if 'laser' not in configs['paths']['psf']:
            exit(f'error: no psf files provided')

        for key, val in configs['paths']['psf']['laser'].items():
            p = partial_path / val
            if not p.is_file():
                exit(f'error: laser %s psf file \'%s\' does not exist' % (key, p))

    return configs

def get_processed_json(path):
    if path.is_file():
        with path.open(mode='r') as f:
            try:
                processed = json.load(f)
            except json.JSONDecodeError as e:
                exit(f'error: \'%s\' is not formatted as a proper JSON file...\n%s' % (path, e))
        
        return processed
    else:
        return {}

def get_dirs(path, excludes):
    unprocessed_dirs = []

    for root, dirs, files in os.walk(path):
        root = Path(root)

        # update directories to prevent us from traversing processed data
        dirs[:] = [d for d in dirs if (root / d) not in excludes]

        # check if root contains a Settings.txt file
        for f in files:
            if f.endswith('Settings.txt'):
                unprocessed_dirs.append(root)
                break

    return unprocessed_dirs

def tag_filename(filename, string):
    f = PurePath(filename)
    return f.stem + string + f.suffix


def process(dirs, configs, dryrun=False, verbose=False):
    processed = {}
    params_bsub = {
        '-J': 'llsm-pipeline',
        '-o': '/dev/null',
        '-We': 10,
        '-n': 4
    }
    params_deskew = {}
    params_decon = {}

    # update default params with user defined configs
    if 'bsub' in configs:
        params_bsub.update(configs['bsub'])
    if 'deskew' in configs:
        params_deskew.update(configs['deskew'])
    if 'decon' in configs:
        params_decon.update(configs['decon'])

    # build commands
    s = ' '
    cmd_bsub = 'bsub'
    for key, val in params_bsub.items():
        cmd_bsub = s.join([cmd_bsub, s.join([key,str(val)])])
    cmd_deskew = 'deskew'
    for key, val in params_deskew.items():
        cmd_deskew = s.join([cmd_deskew, s.join([key,str(val)])])
    cmd_decon = 'decon'
    for key, val in params_decon.items():
        cmd_decon = s.join([cmd_decon, s.join([key,str(val)])])

    # process each directory
    for d in dirs:
        print(f'processing \'%s\'...' % d)

        # get list of files
        files = os.listdir(d)

        # parse the first Settings.txt we find
        for f in reversed(files):
            if f.endswith('Settings.txt'):
                print(f'parsing \'%s\'...' % f)
                settings = settings2json.parse_txt(d / f)
                pattern = re.compile(f.split('_')[0] + '.*_ch(\d+).*\.tif')
                break

        # deskew setup
        if 'waveform' not in settings:
            exit(f'error: settings file did not contain a Waveform section')
        if 'z-motion' not in settings['waveform']:
            exit(f'error: settings file did not contain a Z motion field')

        deskew = False
        if settings['waveform']['z-motion'] == 'Sample piezo':
            if 's-piezo' not in settings['waveform']:
                exit(f'error: settings file did not contain a S Piezo field')
            if 'interval' not in settings['waveform']['s-piezo']:
                exit(f'error: settings file did not contain a S Piezo Interval field')

            deskew = True

            # get step sizes
            steps = settings['waveform']['s-piezo']['interval']

            # deskew output directory
            output_deskew = d / 'deskew'
            if not dryrun:
                output_deskew.mkdir(exist_ok=True)

        # decon setup
        decon = False
        if params_decon:
            # TODO: check parameters

            decon = True

            # decon output directory
            output_decon = d / 'decon'
            if not dryrun:
                output_decon.mkdir(exist_ok=True)

        # process all files in directory
        for f in files:
            m = pattern.fullmatch(f)
            if m:
                ch = int(m.group(1))
                cmd = [cmd_bsub]

                if deskew:
                    outpath = output_deskew / tag_filename(f, '_deskew')
                    tmp = cmd_deskew + f' -w -s %d -o %s %s;' % (steps[ch], outpath , d / f)
                    cmd.append(tmp)
                if decon:
                    cmd.append(cmd_decon)
                if len(cmd) > 1:
                    cmd = s.join(cmd) 
                    if verbose:
                        print(cmd)
                    # if not dryrun:
                    #     exec(cmd)
        
        # update processed list
        d = str(d)
        if deskew or decon:
            processed[d] = {}
        if deskew:
            processed[d]['deskew'] = params_deskew
        if decon:
            processed[d]['decon'] = params_decon

    return  processed

if __name__ == '__main__':
    # get command line arguments
    args = parse_args()

    # load config file
    configs = load_configs(args.input)

    # get dictionary of processed files
    root_dir = Path(configs['paths']['root'])
    processed_path = root_dir / 'processed.json'
    processed_json = get_processed_json(processed_path)

    # get all directories containing a Settings files
    excludes = list(processed_json)
    if configs['paths']['psf']['dir'] != None:
        excludes.append(root_dir / configs['paths']['psf']['dir'])

    unprocessed_dirs = get_dirs(root_dir, excludes)

    # process images in new diectories
    processed_dirs = process(unprocessed_dirs, configs, dryrun=args.dryrun, verbose=args.verbose)

    # update processed.json
    processed_json.update(processed_dirs)
    if not args.dryrun:
        with processed_path.open(mode='w') as f:
            f.write(json.dumps(processed_json, indent=4))

    if args.verbose:
        print('processed.json...')
        print(json.dumps(processed_json, indent=4))

    print('Done')
