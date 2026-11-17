"""Deploy the built native controller DLL closure next to its EXEs (also works under an MSIX parent)."""
import argparse
from pathlib import Path
from native_runtime import stage_runtime


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-root',type=Path,default=root/'ros2_ws/install_control')
    parser.add_argument('--package',default='chassis_controllers')
    parser.add_argument('--dependency-package',action='append')
    args=parser.parse_args()
    install=args.install_root.resolve()
    destination=install/args.package/'lib'/args.package
    assert destination.is_dir() and destination.resolve().is_relative_to(root)
    dependencies=args.dependency_package if args.dependency_package is not None else ['custom_msgs']
    package_sources=[install/name/'bin' for name in [args.package,*dependencies]]
    plugins=[p for folder in package_sources for p in folder.glob('*.dll') if '__rosidl_generator_py' not in p.name]
    stage_runtime(destination,list(destination.glob('*.exe')),package_sources,plugins)


if __name__=='__main__':main()
