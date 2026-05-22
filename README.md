# CP2K Lab Runner

The CP2K Lab Runner allows to submit compute jobs directly from [CP2K Lab](https://lab.cp2k.com) to an HPC cluster.

The runner is installed in the computing center and from there it connects to the CP2K Lab server. This architecture was inspired by [GitLab Runner](https://docs.gitlab.com/runner).

## Installation

1. Log in to your cluster's login node.

2. Download the software:

   ```shell
   $ git clone https://github.com/cp2klab/cp2klab-runner.git .
   ```

3. Copy the example config:
    ```shell
   $ cp cp2klab-runner.conf.example cp2klab-runner.conf
   ```

4. Create a base directory on scratch for the runner to store its files.

5. Check if there is already a suitable job template in [profiles/](./profiles/), or otherwise write your own:
    ```shell
    #!/bin/bash
    #SBATCH -N {num_nodes}
    #SBATCH -t {walltime}
    ...
    srun cp2k.psmp {in_file} &>> {out_file}
    ```

6. Login to CP2K Lab and open the **Runners** app in the account menu at the top right corner.

7. Click **Add new runner**, choose a new name, and click **Create runner**.

8. Click **Copy token** on the newly created runner.

9. Paste the API token from the **clickboard** into the **cp2klab-runner.conf**.

10. Start the runner:
    ```shell
    $ ./cp2klab-runner.py
    CP2K Lab Runner active :-)
    ```

11. (optional) Use the [screen](https://help.ubuntu.com/community/Screen) command to keep the runner active after logout.
