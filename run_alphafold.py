# Copyright 2021 DeepMind Technologies Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Full AlphaFold protein structure prediction script."""
import json
import os
import pathlib
import pickle
import random
import sys
import time
from typing import Dict

from absl import app
from absl import flags
from absl import logging
from alphafold.common import protein
from alphafold.common import residue_constants
from alphafold.data import pipeline
from alphafold.data import templates
from alphafold.model import data
from alphafold.model import config
from alphafold.model import model
from alphafold.relax import relax
import numpy as np

# WTTAT added
from docker import types
import boto3
s3 = boto3.client('s3')
from typing import Tuple


FLAGS = flags.FLAGS
flags.DEFINE_string('BATCH_BUCKET', None, 'S3 bucket')
############## From run_docker.py #############
#### USER CONFIGURATION ####

# Set to target of scripts/download_all_databases.sh

_ROOT_MOUNT_DIRECTORY = '/mnt/'

flags.DEFINE_string('DOWNLOAD_DIR', None, 'dataset folder')

# DOWNLOAD_DIR = '/fsx/dataset/'

# Name of the AlphaFold Docker image.
docker_image_name = 'alphafold_batch'

# Path to a directory that will store the results.
output_dir = '/tmp/alphafold'

# Names of models to use.
# model_names = [
#     'model_1',
#     'model_2',
#     'model_3',
#     'model_4',
#     'model_5',
# ]

# You can individually override the following paths if you have placed the
# data in locations other than the DOWNLOAD_DIR.

# Path to directory of supporting data, contains 'params' dir.
data_dir = FLAGS.DOWNLOAD_DIR

# Path to the Uniref90 database for use by JackHMMER.
uniref90_database_path = os.path.join(
    FLAGS.DOWNLOAD_DIR, 'uniref90', 'uniref90.fasta')

# Path to the MGnify database for use by JackHMMER.
mgnify_database_path = os.path.join(
    FLAGS.DOWNLOAD_DIR, 'mgnify', 'mgy_clusters_2018_12.fa')

# Path to the BFD database for use by HHblits.
bfd_database_path = os.path.join(
    FLAGS.DOWNLOAD_DIR, 'bfd',
    'bfd_metaclust_clu_complete_id30_c90_final_seq.sorted_opt')

# Path to the Small BFD database for use by JackHMMER.
small_bfd_database_path = os.path.join(
    FLAGS.DOWNLOAD_DIR, 'small_bfd', 'bfd-first_non_consensus_sequences.fasta')

# Path to the Uniclust30 database for use by HHblits.
uniclust30_database_path = os.path.join(
    FLAGS.DOWNLOAD_DIR, 'uniclust30', 'uniclust30_2018_08', 'uniclust30_2018_08')

# Path to the PDB70 database for use by HHsearch.
pdb70_database_path = os.path.join(FLAGS.DOWNLOAD_DIR, 'pdb70', 'pdb70')

# Path to a directory with template mmCIF structures, each named <pdb_id>.cif')
template_mmcif_dir = os.path.join(FLAGS.DOWNLOAD_DIR, 'pdb_mmcif', 'mmcif_files')

# Path to a file mapping obsolete PDB IDs to their replacements.
obsolete_pdbs_path = os.path.join(FLAGS.DOWNLOAD_DIR, 'pdb_mmcif', 'obsolete.dat')

#### END OF USER CONFIGURATION ####

# flags.DEFINE_bool('use_gpu', True, 'Enable NVIDIA runtime to run with GPUs.')
# flags.DEFINE_string('gpu_devices', 'all', 'Comma separated list of devices to '
#                     'pass to NVIDIA_VISIBLE_DEVICES.')
flags.DEFINE_list('fasta_paths', None, 'Paths to FASTA files, each containing '
                  'one sequence. Paths should be separated by commas. '
                  'All FASTA paths must have a unique basename as the '
                  'basename is used to name the output directories for '
                  'each prediction.')
flags.DEFINE_string('max_template_date', None, 'Maximum template release date '
                    'to consider (ISO-8601 format - i.e. YYYY-MM-DD). '
                    'Important if folding historical test sets.')
flags.DEFINE_enum('preset', 'full_dbs',
                  ['reduced_dbs', 'full_dbs', 'casp14'],
                  'Choose preset model configuration - no ensembling and '
                  'smaller genetic database config (reduced_dbs), no '
                  'ensembling and full genetic database config  (full_dbs) or '
                  'full genetic database config and 8 model ensemblings '
                  '(casp14).')
flags.DEFINE_boolean('benchmark', False, 'Run multiple JAX model evaluations '
                     'to obtain a timing that excludes the compilation time, '
                     'which should be more indicative of the time required for '
                     'inferencing many proteins.')

############## From run_docker.py #############
# Internal import (7716).

# flags.DEFINE_list('fasta_paths', None, 'Paths to FASTA files, each containing '
#                   'one sequence. Paths should be separated by commas. '
#                   'All FASTA paths must have a unique basename as the '
#                   'basename is used to name the output directories for '
#                   'each prediction.')
# flags.DEFINE_string('output_dir', None, 'Path to a directory that will '
#                     'store the results.')
flags.DEFINE_list('model_names', None, 'Names of models to use.')

# 以下参数不需要从run_docker.py传了
# flags.DEFINE_string('data_dir', None, 'Path to directory of supporting data.')
# flags.DEFINE_string('jackhmmer_binary_path', '/usr/bin/jackhmmer',
#                     'Path to the JackHMMER executable.')
# flags.DEFINE_string('hhblits_binary_path', '/usr/bin/hhblits',
#                     'Path to the HHblits executable.')
# flags.DEFINE_string('hhsearch_binary_path', '/usr/bin/hhsearch',
#                     'Path to the HHsearch executable.')
# flags.DEFINE_string('kalign_binary_path', '/usr/bin/kalign',
#                     'Path to the Kalign executable.')
# flags.DEFINE_string('uniref90_database_path', None, 'Path to the Uniref90 '
#                     'database for use by JackHMMER.')
# flags.DEFINE_string('mgnify_database_path', None, 'Path to the MGnify '
#                     'database for use by JackHMMER.')
# flags.DEFINE_string('bfd_database_path', None, 'Path to the BFD '
#                     'database for use by HHblits.')
# flags.DEFINE_string('small_bfd_database_path', None, 'Path to the small '
#                     'version of BFD used with the "reduced_dbs" preset.')
# flags.DEFINE_string('uniclust30_database_path', None, 'Path to the Uniclust30 '
#                     'database for use by HHblits.')
# flags.DEFINE_string('pdb70_database_path', None, 'Path to the PDB70 '
#                     'database for use by HHsearch.')
# flags.DEFINE_string('template_mmcif_dir', None, 'Path to a directory with '
#                     'template mmCIF structures, each named <pdb_id>.cif')
# flags.DEFINE_string('max_template_date', None, 'Maximum template release date '
#                     'to consider. Important if folding historical test sets.')
# flags.DEFINE_string('obsolete_pdbs_path', None, 'Path to file containing a '
#                     'mapping from obsolete PDB IDs to the PDB IDs of their '
#                     'replacements.')
# flags.DEFINE_enum('preset', 'full_dbs',
#                   ['reduced_dbs', 'full_dbs', 'casp14'],
#                   'Choose preset model configuration - no ensembling and '
#                   'smaller genetic database config (reduced_dbs), no '
#                   'ensembling and full genetic database config  (full_dbs) or '
#                   'full genetic database config and 8 model ensemblings '
#                   '(casp14).')
# flags.DEFINE_boolean('benchmark', False, 'Run multiple JAX model evaluations '
#                      'to obtain a timing that excludes the compilation time, '
#                      'which should be more indicative of the time required for '
#                      'inferencing many proteins.')

flags.DEFINE_integer('random_seed', None, 'The random seed for the data '
                     'pipeline. By default, this is randomly generated. Note '
                     'that even if this is set, Alphafold may still not be '
                     'deterministic, because processes like GPU inference are '
                     'nondeterministic.')
FLAGS = flags.FLAGS

MAX_TEMPLATE_HITS = 20
RELAX_MAX_ITERATIONS = 0
RELAX_ENERGY_TOLERANCE = 2.39
RELAX_STIFFNESS = 10.0
RELAX_EXCLUDE_RESIDUES = []
RELAX_MAX_OUTER_ITERATIONS = 20


def _check_flag(flag_name: str, preset: str, should_be_set: bool):
  if should_be_set != bool(FLAGS[flag_name].value):
    verb = 'be' if should_be_set else 'not be'
    raise ValueError(f'{flag_name} must {verb} set for preset "{preset}"')


def predict_structure(
    fasta_path: str,
    fasta_name: str,
    output_dir_base: str,
    data_pipeline: pipeline.DataPipeline,
    model_runners: Dict[str, model.RunModel],
    amber_relaxer: relax.AmberRelaxation,
    benchmark: bool,
    random_seed: int):
  """Predicts structure using AlphaFold for the given sequence."""
  timings = {}
  output_dir = os.path.join(output_dir_base, fasta_name)
  if not os.path.exists(output_dir):
    os.makedirs(output_dir)
  msa_output_dir = os.path.join(output_dir, 'msas')
  if not os.path.exists(msa_output_dir):
    os.makedirs(msa_output_dir)

  # Get features.
  t_0 = time.time()
  feature_dict = data_pipeline.process(
      input_fasta_path=fasta_path,
      msa_output_dir=msa_output_dir)
  timings['features'] = time.time() - t_0

  # Write out features as a pickled dictionary.
  features_output_path = os.path.join(output_dir, 'features.pkl')
  with open(features_output_path, 'wb') as f:
    pickle.dump(feature_dict, f, protocol=4)

  relaxed_pdbs = {}
  plddts = {}

  # Run the models.
  for model_name, model_runner in model_runners.items():
    logging.info('Running model %s', model_name)
    t_0 = time.time()
    processed_feature_dict = model_runner.process_features(
        feature_dict, random_seed=random_seed)
    timings[f'process_features_{model_name}'] = time.time() - t_0

    t_0 = time.time()
    prediction_result = model_runner.predict(processed_feature_dict)
    t_diff = time.time() - t_0
    timings[f'predict_and_compile_{model_name}'] = t_diff
    logging.info(
        'Total JAX model %s predict time (includes compilation time, see --benchmark): %.0f?',
        model_name, t_diff)

    if benchmark:
      t_0 = time.time()
      model_runner.predict(processed_feature_dict)
      timings[f'predict_benchmark_{model_name}'] = time.time() - t_0

    # Get mean pLDDT confidence metric.
    plddt = prediction_result['plddt']
    plddts[model_name] = np.mean(plddt)

    # Save the model outputs.
    result_output_path = os.path.join(output_dir, f'result_{model_name}.pkl')
    with open(result_output_path, 'wb') as f:
      pickle.dump(prediction_result, f, protocol=4)

    # Add the predicted LDDT in the b-factor column.
    # Note that higher predicted LDDT value means higher model confidence.
    plddt_b_factors = np.repeat(
        plddt[:, None], residue_constants.atom_type_num, axis=-1)
    unrelaxed_protein = protein.from_prediction(
        features=processed_feature_dict,
        result=prediction_result,
        b_factors=plddt_b_factors)

    unrelaxed_pdb_path = os.path.join(output_dir, f'unrelaxed_{model_name}.pdb')
    with open(unrelaxed_pdb_path, 'w') as f:
      f.write(protein.to_pdb(unrelaxed_protein))

    # Relax the prediction.
    t_0 = time.time()
    relaxed_pdb_str, _, _ = amber_relaxer.process(prot=unrelaxed_protein)
    timings[f'relax_{model_name}'] = time.time() - t_0

    relaxed_pdbs[model_name] = relaxed_pdb_str

    # Save the relaxed PDB.
    relaxed_output_path = os.path.join(output_dir, f'relaxed_{model_name}.pdb')
    with open(relaxed_output_path, 'w') as f:
      f.write(relaxed_pdb_str)

  # Rank by pLDDT and write out relaxed PDBs in rank order.
  ranked_order = []
  for idx, (model_name, _) in enumerate(
      sorted(plddts.items(), key=lambda x: x[1], reverse=True)):
    ranked_order.append(model_name)
    ranked_output_path = os.path.join(output_dir, f'ranked_{idx}.pdb')
    with open(ranked_output_path, 'w') as f:
      f.write(relaxed_pdbs[model_name])

  ranking_output_path = os.path.join(output_dir, 'ranking_debug.json')
  with open(ranking_output_path, 'w') as f:
    f.write(json.dumps({'plddts': plddts, 'order': ranked_order}, indent=4))

  logging.info('Final timings for %s: %s', fasta_name, timings)

  timings_output_path = os.path.join(output_dir, 'timings.json')
  with open(timings_output_path, 'w') as f:
    f.write(json.dumps(timings, indent=4))
  

  ######
  #  将生成数据上传到S3 output文件夹
  #  需要$BATCH_BUCKET 环境变量
  print('start uploading')

  for root,dirs,files in os.walk(output_dir):
    for file in files:
        s3.upload_file(os.path.join(root,file),FLAGS.BATCH_BUCKET,'output/'+fasta_name+'/'+file)

  print('upload successed to '+ FLAGS.BATCH_BUCKET,'output/'+fasta_name+'/')
  ######

# From run_docker.py 挂载文件夹
def _create_mount(mount_name: str, path: str) -> Tuple[types.Mount, str]:
  path = os.path.abspath(path)
  source_path = os.path.dirname(path)
  target_path = os.path.join(_ROOT_MOUNT_DIRECTORY, mount_name)
  logging.info('Mounting %s -> %s', source_path, target_path)
  mount = types.Mount(target_path, source_path, type='bind', read_only=True)
  return mount, os.path.join(target_path, os.path.basename(path))

def main(argv):
  if len(argv) > 1:
    raise app.UsageError('Too many command-line arguments.')

  use_small_bfd = FLAGS.preset == 'reduced_dbs'
  _check_flag('small_bfd_database_path', FLAGS.preset,
              should_be_set=use_small_bfd)
  _check_flag('bfd_database_path', FLAGS.preset,
              should_be_set=not use_small_bfd)
  _check_flag('uniclust30_database_path', FLAGS.preset,
              should_be_set=not use_small_bfd)

  if FLAGS.preset in ('reduced_dbs', 'full_dbs'):
    num_ensemble = 1
  elif FLAGS.preset == 'casp14':
    num_ensemble = 8

  # Check for duplicate FASTA file names.
  fasta_names = [pathlib.Path(p).stem for p in FLAGS.fasta_paths]
  if len(fasta_names) != len(set(fasta_names)):
    raise ValueError('All FASTA paths must have a unique basename.')

######
#  判断是否为S3 URL，将s3数据的下载fasta文件到本地，并且将S3 URL替换为文件名
#  by WTTAT
  from urllib.parse import urlparse

  for i,paths in enumerate(FLAGS.fasta_paths):
    if paths.startswith("s3://"):
        o = urlparse(paths)
        bucket = o.netloc
        key = o.path
        file_name = paths.split("/")[-1]
        print('downloading fasta file from '+paths+' as '+file_name)
        s3.download_file(bucket,key.lstrip('/'),file_name)
        print('download file success')
        FLAGS.fasta_paths[i]=file_name
######

# From run_docker.py
  mounts = []
  # command_args = []

  target_fasta_paths = []

  # 挂载
  for i, fasta_path in enumerate(FLAGS.fasta_paths):
    mount, target_path = _create_mount(f'fasta_path_{i}', fasta_path)
    mounts.append(mount)
    target_fasta_paths.append(target_path)
  # command_args.append(f'--fasta_paths={",".join(target_fasta_paths)}')
  
  database_paths = [
      ('uniref90_database_path', uniref90_database_path),
      ('mgnify_database_path', mgnify_database_path),
      ('pdb70_database_path', pdb70_database_path),
      ('data_dir', data_dir),
      ('template_mmcif_dir', template_mmcif_dir),
      ('obsolete_pdbs_path', obsolete_pdbs_path),
  ]
  if FLAGS.preset == 'reduced_dbs':
    database_paths.append(('small_bfd_database_path', small_bfd_database_path))
  else:
    database_paths.extend([
        ('uniclust30_database_path', uniclust30_database_path),
        ('bfd_database_path', bfd_database_path),
    ])
  for name, path in database_paths:
    if path:
      mount, target_path = _create_mount(name, path)
      mounts.append(mount)
      # command_args.append(f'--{name}={target_path}')

  output_target_path = os.path.join(_ROOT_MOUNT_DIRECTORY, 'output')
  mounts.append(types.Mount(output_target_path, output_dir, type='bind'))

  command_args.extend([
      f'--output_dir={output_target_path}',
      # f'--model_names={",".join(model_names)}',
      f'--model_names={",".join(FLAGS.model_names)}',
      f'--max_template_date={FLAGS.max_template_date}',
      f'--preset={FLAGS.preset}',
      f'--benchmark={FLAGS.benchmark}',
      '--logtostderr',
  ])

  template_featurizer = templates.TemplateHitFeaturizer(
      mmcif_dir=FLAGS.template_mmcif_dir,
      max_template_date=FLAGS.max_template_date,
      max_hits=MAX_TEMPLATE_HITS,
      kalign_binary_path=FLAGS.kalign_binary_path,
      release_dates_path=None,
      obsolete_pdbs_path=FLAGS.obsolete_pdbs_path)

  data_pipeline = pipeline.DataPipeline(
      jackhmmer_binary_path=FLAGS.jackhmmer_binary_path,
      hhblits_binary_path=FLAGS.hhblits_binary_path,
      hhsearch_binary_path=FLAGS.hhsearch_binary_path,
      uniref90_database_path=FLAGS.uniref90_database_path,
      mgnify_database_path=FLAGS.mgnify_database_path,
      bfd_database_path=FLAGS.bfd_database_path,
      uniclust30_database_path=FLAGS.uniclust30_database_path,
      small_bfd_database_path=FLAGS.small_bfd_database_path,
      pdb70_database_path=FLAGS.pdb70_database_path,
      template_featurizer=template_featurizer,
      use_small_bfd=use_small_bfd)

  model_runners = {}
  for model_name in FLAGS.model_names:
    model_config = config.model_config(model_name)
    model_config.data.eval.num_ensemble = num_ensemble
    model_params = data.get_model_haiku_params(
        # model_name=model_name, data_dir=FLAGS.data_dir)
        model_name=model_name, data_dir=data_dir)
    model_runner = model.RunModel(model_config, model_params)
    model_runners[model_name] = model_runner

  logging.info('Have %d models: %s', len(model_runners),
               list(model_runners.keys()))

  amber_relaxer = relax.AmberRelaxation(
      max_iterations=RELAX_MAX_ITERATIONS,
      tolerance=RELAX_ENERGY_TOLERANCE,
      stiffness=RELAX_STIFFNESS,
      exclude_residues=RELAX_EXCLUDE_RESIDUES,
      max_outer_iterations=RELAX_MAX_OUTER_ITERATIONS)

  random_seed = FLAGS.random_seed
  if random_seed is None:
    random_seed = random.randrange(sys.maxsize)
  logging.info('Using random seed %d for the data pipeline', random_seed)

  # Predict structure for each of the sequences.

  # for fasta_path, fasta_name in zip(FLAGS.fasta_paths, fasta_names):
  for fasta_path, fasta_name in zip(target_fasta_paths, fasta_names):

    predict_structure(
        fasta_path=fasta_path,

        fasta_name=fasta_name,

        # output_dir_base=FLAGS.output_dir,
        output_dir_base=output_dir,

        data_pipeline=data_pipeline,
        model_runners=model_runners,
        amber_relaxer=amber_relaxer,
        benchmark=FLAGS.benchmark,
        random_seed=random_seed)
  
if __name__ == '__main__':
  flags.mark_flags_as_required([
      'BATCH_BUCKET',
      'DOWNLOAD_DIR',
      'fasta_paths', # now support S3 path
      # 'output_dir',
      'model_names',
      # 'data_dir',
      'preset',
      # 'uniref90_database_path',
      # 'mgnify_database_path',
      # 'pdb70_database_path',
      # 'template_mmcif_dir',
      'max_template_date',
      # 'obsolete_pdbs_path',
  ])

  app.run(main)
