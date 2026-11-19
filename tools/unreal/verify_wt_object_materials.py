"""Read-only native reload of the saved layered Surface graph and all 192 source MIs."""
import re
from pathlib import Path
import sys
import unreal
sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_wt_object_materials import update

if __name__ == '__main__':
    command = unreal.SystemLibrary.get_command_line()
    source = re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))', command)
    output = re.search(r'-ObjectMaterialAuditOutput=(?:"([^"]+)"|(\S+))', command)
    if not source or not output:
        raise ValueError('pass -SceneSource and -ObjectMaterialAuditOutput')
    update(source.group(1) or source.group(2), output.group(1) or output.group(2), author=False)
