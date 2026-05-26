#!/bin/bash -l

#SBATCH --account={account}
#SBATCH --partition={partition}
#SBATCH --time={walltime}
#SBATCH --nodes={num_nodes}
#SBATCH --ntasks-per-core=1
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=4
#SBATCH --hint=nomultithread
#SBATCH --hint=exclusive
#SBATCH --constraint=mc
#SBATCH --uenv=cp2k/2026.1:v1
#SBATCH --view=cp2k

# Pull and start uenv image
# https://docs.cscs.ch/software/uenv/#quick-start
#
# $ uenv image pull cp2k/2026.1:v1
# $ uenv start cp2k/2026.1:v1

# https://docs.cscs.ch/software/sciapps/cp2k/#running-on-eiger
export OMP_NUM_THREADS=$((SLURM_CPUS_PER_TASK - 1)) 

ulimit -s unlimited
srun --cpu-bind=socket cp2k.psmp {in_file} &>> {out_file}

# EOF
