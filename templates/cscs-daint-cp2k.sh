#!/bin/bash -l

#SBATCH --account={account}
#SBATCH --partition={partition}
#SBATCH --time={walltime}
#SBATCH --nodes={num_nodes}
#SBATCH --ntasks-per-core=1
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=8
#SBATCH --hint=nomultithread
#SBATCH --hint=exclusive
#SBATCH --no-requeue
#SBATCH --uenv=cp2k/2026.1:v1
#SBATCH --view=cp2k

# Copy mps-wrapper.sh script into $HOME/bin.
# https://docs.cscs.ch/running/slurm/#multiple-ranks-per-gpu

# Pull and start uenv image
# https://docs.cscs.ch/software/uenv/#quick-start
#
# $ uenv image pull cp2k/2026.1:v1
# $ uenv start cp2k/2026.1:v1

# https://docs.cscs.ch/software/sciapps/cp2k/#running-on-daint

export CUDA_CACHE_PATH="/dev/shm/$USER/cuda_cache"
export MPICH_GPU_SUPPORT_ENABLED=1
export MPICH_MALLOC_FALLBACK=1
export OMP_NUM_THREADS=$((SLURM_CPUS_PER_TASK - 1))

ulimit -s unlimited
srun --cpu-bind=socket $HOME/bin/mps-wrapper.sh cp2k.psmp {in_file} &>> {out_file}

# EOF
