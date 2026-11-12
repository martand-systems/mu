# Reproduce s2-locks/env.explicit.txt (run from s2-locks/work; env.sh sets HOME/MAMBA_ROOT_PREFIX/MM/UV into scratch)
source ./env.sh            # also exports CONDA_OVERRIDE_GLIBC=2.28 CONDA_OVERRIDE_LINUX=5.15 CONDA_OVERRIDE_CUDA=
# 1. osx_pins.tsv = name/version/build/subdir parsed from toolchain/osx-arm64/env.explicit.txt
# 2. pinrc.yaml   = pinned_packages: "<name>==<osx version>" for every osx package EXCEPT
#    libintl moltenvk libopenvino-arm-cpu-plugin pyobjc-core pyobjc-framework-cocoa libcxx llvm-openmp libhwloc eigen-abi
./solve.sh F.json "libhwloc eigen-abi" \
  python=3.13.18 ocp=8.0.1.1 vtk=9.7.0 vtk-base=9.7.0 gmsh=4.15.2 python-gmsh=4.15.2 calculix=2.23 \
  pyvista meshio numpy scipy matplotlib-base tornado ccx2paraview numba nlopt casadi git-lfs pip setuptools \
  'libblas=*=*openblas' 'libopenblas=*=openmp*' '_openmp_mutex=*=*_llvm' mesa-llvmpipe libegl
# solve.sh runs:
#   $MM create --rc-file pinrc.yaml --platform linux-64 --dry-run --json -y -c conda-forge --override-channels -p ./x <specs>
# then: LINK actions -> topologically sorted "url#md5" lines under "@EXPLICIT" (see the python block in the session)
# verify: $MM create --platform linux-64 --dry-run --json -y -p ./xe --file ../env.explicit.txt   (339 LINK, success)
