"""Stage a bounded native executable/plugin DLL closure inside the workspace."""
import hashlib,json,os,re,shutil,subprocess
from pathlib import Path

def stage_runtime(destination,executables,library_dirs,plugins=()):
    root=Path(__file__).resolve().parents[2];destination=Path(destination).resolve()
    assert destination.is_relative_to(root)
    destination.mkdir(parents=True,exist_ok=True)
    prefix=Path(os.environ['CONDA_PREFIX']);system=Path(os.environ['SystemRoot'])/'System32'
    sources=[*map(Path,library_dirs),prefix/'Library/bin',prefix,system]
    dump=shutil.which('dumpbin');assert dump,'Run from the native Visual Studio environment.'
    pending=[*map(Path,executables),*map(Path,plugins),prefix/'Library/bin/rmw_fastrtps_cpp.dll']
    seen={};copied=[];programs=[]
    while pending:
        path=pending.pop();assert path.is_file(),path
        name=path.name.lower();digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if name in seen:
            if seen[name]!=digest:raise RuntimeError('Distinct native binaries have the same name: '+name)
            continue
        seen[name]=digest
        for language in ('c','cpp'):
            suffix='__rosidl_typesupport_'+language+'.dll'
            if name.endswith(suffix):
                for backend in ('fastrtps','introspection'):
                    companion=path.name[:-len(suffix)]+'__rosidl_typesupport_'+backend+'_'+language+'.dll'
                    found=next((folder/companion for folder in sources if (folder/companion).is_file()),None)
                    if found is not None:pending.append(found)
        target=destination/path.name
        if path.resolve()!=target.resolve():shutil.copy2(path,target)
        entry=dict(source=str(path),file=path.name,sha256=digest)
        (programs if path.suffix.lower()=='.exe' else copied).append(entry)
        output=subprocess.check_output([dump,'/DEPENDENTS',str(path)],text=True,errors='replace')
        for dependency in re.findall(r'^\s+([\w.+-]+\.dll)\s*$',output,re.M|re.I):
            if dependency.lower().startswith(('api-ms-','ext-ms-')):continue
            found=next((folder/dependency for folder in sources if (folder/dependency).is_file()),None)
            if found is None:raise RuntimeError(f'Missing dependency: {path.name} -> {dependency}')
            if found.parent!=system:pending.append(found)
    report=dict(scope='local native runtime DLL deployment; no system DLLs copied or global PATH changes',programs=programs,dlls=copied)
    (destination/'runtime-dependencies.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Staged native runtime:',len(programs),'programs',len(copied),'DLLs',sum((destination/x['file']).stat().st_size for x in copied),'bytes')
    return report
