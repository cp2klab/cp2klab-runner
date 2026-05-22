#!/bin/bash
#SBATCH -N {num_nodes}
#SBATCH -t {walltime}
#SBATCH --exclusive
#SBATCH --ntasks-per-node=48
#SBATCH --cpus-per-task=4

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export OMP_PLACES=cores
export OMP_PROC_BIND=true

module reset
module load chem/CP2K/2026.1-foss-2025b-cpu
srun cp2k.psmp {in_file} &>> {out_file}

#EOF
