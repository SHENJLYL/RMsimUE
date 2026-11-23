"""Export physical localization surfaces from a map package; consumes no run poses.

The original CAD transform and navigation grid stay fixed. Both documented normal
categories are emitted for reproducible offline comparison; output paths must be new.
"""
import argparse, ast, hashlib, json, math, re, shutil, struct, sys, time, tempfile
from pathlib import Path
import numpy as np

CATEGORIES = (("steep75", 75.0), ("vertical85", 85.0))
ROOT = Path(__file__).resolve().parents[2]

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def read_pgm(path):
    raw = Path(path).read_bytes()
    match = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", raw)
    if not match:
        raise ValueError("Expected project P5/255 raster")
    width, height = map(int, match.groups())
    data = raw[match.end():]
    if len(data) != width * height:
        raise ValueError("Raster dimensions do not match bytes")
    return np.flipud(np.frombuffer(data, dtype=np.uint8).reshape(height, width)).copy()

def clip_y(vertices, bound, greater):
    if not vertices:
        return []
    output = []
    previous = vertices[-1]
    previous_in = previous[1] >= bound if greater else previous[1] <= bound
    for current in vertices:
        current_in = current[1] >= bound if greater else current[1] <= bound
        if current_in != previous_in:
            fraction = (bound - previous[1]) / (current[1] - previous[1])
            output.append((previous[0] + fraction * (current[0] - previous[0]), bound))
        if current_in:
            output.append(current)
        previous, previous_in = current, current_in
    return output

def projected_cells(triangle_xy, origin, resolution, width, height):
    """Conservative intersection of a physical triangle projection and closed grid cells."""
    vertices = [(float(p[0]), float(p[1])) for p in triangle_xy]
    epsilon = 1e-9  # metres; resolves exact shared boundaries without an origin offset
    low = min(p[1] for p in vertices); high = max(p[1] for p in vertices)
    first = max(0, math.floor((low-origin[1]-epsilon)/resolution))
    last = min(height-1, math.floor((high-origin[1]+epsilon)/resolution))
    for row in range(first, last+1):
        ymin = origin[1]+row*resolution; ymax = ymin+resolution
        clipped = clip_y(clip_y(vertices, ymin-epsilon, True), ymax+epsilon, False)
        if not clipped:
            continue
        xmin = min(p[0] for p in clipped); xmax = max(p[0] for p in clipped)
        start = max(0, math.floor((xmin-origin[0]-epsilon)/resolution))
        stop = min(width-1, math.floor((xmax-origin[0]+epsilon)/resolution))
        for col in range(start, stop+1):
            yield row, col

def self_test():
    # A vertical physical facade projects to x=.25; a shared-edge facade to x=.2.
    assert set(projected_cells([(0.25,0.11),(0.25,0.19),(0.25,0.14)], (0,0), .1, 5,5)) == {(1,2)}
    assert set(projected_cells([(0.2,0.11),(0.2,0.19),(0.2,0.14)], (0,0), .1, 5,5)) == {(1,1),(1,2)}
    tri = np.array([[.11,.11,0.],[.39,.39,0.],[.11,.11,1.]])
    cells = set(projected_cells(tri[:,:2], (0,0), .1,5,5))
    for t in np.linspace(0,1,101):
        xy = tri[0,:2]*(1-t) + tri[1,:2]*t
        assert (math.floor(xy[1]/.1), math.floor(xy[0]/.1)) in cells
    # Translation of both geometry and declared origin preserves indices exactly.
    shifted = tri[:,:2] + np.array([-14.,-8.05])
    assert cells == set(projected_cells(shifted, (-14.,-8.05), .1,5,5))
    n = np.cross(tri[1]-tri[0], tri[2]-tri[0]); n /= np.linalg.norm(n)
    assert abs(n[2]) == 0.0
    wave_normal_z = math.cos(math.radians(42.0))
    assert wave_normal_z > math.cos(math.radians(75.0))
    assert wave_normal_z > math.cos(math.radians(85.0))
    print(json.dumps({"status":"passed","checks":6,"scope":"physical projection, shared boundary, translation and slope category"}), flush=True)

def load_original_geometry_reader(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"read_glb","array","world_primitives"}
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in definitions} != names:
        raise ValueError("Original geometry-reader function inventory changed")
    module = ast.Module(body=definitions, type_ignores=[])
    namespace = {"np":np,"json":json,"struct":struct,"re":re}
    exec(compile(module,str(path),"exec"),namespace)
    return namespace["read_glb"], namespace["world_primitives"]


def restore_package(manifest_path):
    """Recreate only missing derived bytes; approved map metadata stays immutable."""
    sys.path.insert(0, str(ROOT/"ros2_ws/src/rmsim_bridge"))
    from rmsim_bridge.navigation_map_identity import packaged_localization_map, package_relative_path
    manifest_path = Path(manifest_path).resolve()
    specification = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    selected = packaged_localization_map(manifest_path.parent, specification, allow_missing_derived=True)
    if selected is None:
        raise ValueError("Package has no enabled localization map to restore")
    if not selected["missing_derived"]:
        print(json.dumps({"status":"already_verified","yaml_path":selected["yaml_path"]}),flush=True)
        return
    recipe = selected["export_recipe"]
    category = recipe.get("category")
    categories = dict(CATEGORIES)
    if category not in categories or recipe.get("minimum_surface_inclination_from_horizontal_deg") != categories[category]:
        raise ValueError("Unsupported fixed geometry category in package recipe")
    paths = {}
    for key in ("tool","source_collision","descriptor","navigation_map_yaml","navigation_raster","geometry_reader"):
        item = recipe.get(key)
        if not isinstance(item,dict):
            raise ValueError("Missing geometry export input: "+key)
        path = package_relative_path(ROOT,item.get("path"),"export_recipe."+key)
        if not path.is_file() or sha(path) != item.get("sha256"):
            raise ValueError("Geometry export input missing or changed: "+key+" ("+str(path)+")")
        paths[key] = path
    if paths["tool"] != Path(__file__).resolve():
        raise ValueError("Package recipe must use this original map exporter")
    navigation_manifest = package_relative_path(ROOT,recipe.get("navigation_manifest_path"),
                                                "export_recipe.navigation_manifest_path")
    source_spec = json.loads(navigation_manifest.read_text(encoding="utf-8-sig"))
    if (source_spec["scene_geometry_sha256"] != specification["scene_geometry_sha256"] or
            source_spec["raster_sha256"] != specification["raster_sha256"]):
        raise ValueError("Source navigation package differs from requested restoration")
    immutable_keys = ("source_collision_sha256","descriptor_sha256","scene_geometry_sha256",
        "source_step_sha256","source_to_sim_translation_m","coordinate_system","units",
        "width","height","resolution","origin","normal_criterion","height_filter",
        "selected_triangles","occupied_cells","free_cells","unknown_cells")
    restored = []
    with tempfile.TemporaryDirectory(prefix="rmsim-localization-export-") as temporary:
        output = Path(temporary)/"export"
        main(["--output",str(output),"--source",str(paths["source_collision"]),
              "--descriptor",str(paths["descriptor"]),"--navigation-map-yaml",str(paths["navigation_map_yaml"]),
              "--navigation-manifest",str(navigation_manifest)])
        generated = output/category
        generated_metadata = json.loads((generated/"metadata.json").read_text(encoding="utf-8"))
        for key in immutable_keys:
            if generated_metadata.get(key) != selected["metadata"].get(key):
                raise ValueError("Rebuilt geometry semantics differ from approved map: "+key)
        for role,name in (("raster","map.pgm"),("surface_provenance","surface-provenance.npz")):
            if sha(generated/name) != selected["files"][role]["sha256"]:
                raise ValueError("Rebuilt derived bytes differ from approved map: "+role)
        for role in selected["missing_derived"]:
            name = "map.pgm" if role=="raster" else "surface-provenance.npz"
            destination = Path(selected["files"][role]["path"])
            # Exclusive creation protects concurrent preparation and existing inputs.
            with destination.open("xb") as stream:
                stream.write((generated/name).read_bytes())
            restored.append(role)
    packaged_localization_map(manifest_path.parent,specification)
    print(json.dumps({"status":"restored_and_verified","yaml_path":selected["yaml_path"],
                      "restored":restored,"metadata_unchanged":True}),flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-package", type=Path)
    parser.add_argument("--output")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--descriptor", type=Path)
    parser.add_argument("--navigation-map-yaml", type=Path)
    parser.add_argument("--navigation-manifest", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.restore_package:
        restore_package(args.restore_package)
        return
    if args.self_test:
        self_test()
        return
    if not all((args.output, args.source, args.descriptor, args.navigation_map_yaml, args.navigation_manifest)):
        parser.error("--output, --source, --descriptor, --navigation-map-yaml and --navigation-manifest are required")
    out = Path(args.output)
    if out.exists():
        raise FileExistsError("Refusing existing candidate output: "+str(out))
    source = args.source.resolve()
    descriptor = args.descriptor.resolve()
    navigation_yaml = args.navigation_map_yaml.resolve()
    navigation = navigation_yaml.parent
    manifest_path = args.navigation_manifest.resolve()
    if navigation_yaml.name != "map.yaml":
        raise ValueError("Map package requires the stable map.yaml image declaration")
    nav_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor_data = json.loads(descriptor.read_text(encoding="utf-8"))
    info = nav_manifest["map_metadata"]
    hashes = {"source_collision_sha256":sha(source),"descriptor_sha256":sha(descriptor),
              "reference_navigation_raster_sha256":sha(navigation/"map.pgm"),
              "reference_navigation_yaml_sha256":sha(navigation/"map.yaml"),
              "reference_navigation_manifest_sha256":sha(manifest_path)}
    if hashes["source_collision_sha256"] != info["collision_sha256"]:
        raise ValueError("Collision source identity mismatch")
    if hashes["reference_navigation_raster_sha256"] != nav_manifest["raster_sha256"]:
        raise ValueError("Navigation source identity mismatch")
    if descriptor_data["units"] != "m" or descriptor_data["coordinate_system"] != "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP":
        raise ValueError("Unsupported input units or axes")
    resolution = float(info["resolution"]); origin = tuple(info["origin"][:2])
    width, height = int(info["width"]), int(info["height"])
    if resolution <= 0 or len(info["origin"]) != 3 or info["origin"][2] != 0:
        raise ValueError("Expected nonrotated metric raster")
    original_pixels = read_pgm(navigation/"map.pgm")
    if original_pixels.shape != (height,width):
        raise ValueError("Navigation dimensions mismatch")
    original_free = original_pixels >= 254
    reader = ROOT/"tools/maps/prepare_rmuc2027.py"
    read_glb, world_primitives = load_original_geometry_reader(reader)
    document, blob = read_glb(source)
    if document["asset"]["extras"]["source_sha256"] != descriptor_data["source_step_sha256"]:
        raise ValueError("CAD source identity mismatch")
    translation = np.asarray(descriptor_data["source_to_sim_translation_m"],dtype=np.float64)
    if translation.shape != (3,) or not np.isfinite(translation).all():
        raise ValueError("Invalid original source-to-simulation translation")
    out.mkdir(parents=True,exist_ok=False)
    baseline = out/"baseline"; baseline.mkdir()
    baseline_files = [navigation/"map.pgm",navigation/"map.yaml",manifest_path,descriptor]
    baseline_hashes = {}
    for index,path in enumerate(baseline_files):
        target = baseline/f"{index:02d}-{path.name}"
        shutil.copyfile(path,target)
        baseline_hashes[str(path.resolve())] = sha(path)
    (baseline/"manifest.json").write_text(json.dumps({"files":baseline_hashes,**hashes},indent=2)+"\n",encoding="utf-8",newline="\n")
    results = {name:{"cells":{},"triangles":0,"groups":{}} for name,_ in CATEGORIES}
    group_names=[]; triangles_total=0; degenerate=0
    minimum=np.full(3,np.inf);maximum=np.full(3,-np.inf)
    started=time.monotonic()
    for primitive_index,(group,vertices,unused_normals,indices,material) in enumerate(world_primitives(document,blob)):
        if group not in group_names:
            group_names.append(group)
        group_id=group_names.index(group)
        vertices=vertices+translation
        minimum=np.minimum(minimum,vertices.min(axis=0));maximum=np.maximum(maximum,vertices.max(axis=0))
        triangles_total += len(indices)
        for start in range(0,len(indices),32768):
            triangle = vertices[indices[start:start+32768]]
            cross = np.cross(triangle[:,1]-triangle[:,0], triangle[:,2]-triangle[:,0])
            norm = np.linalg.norm(cross,axis=1)
            valid = norm > 1e-14
            degenerate += int((~valid).sum())
            nz = np.ones(len(norm))
            nz[valid] = np.abs(cross[valid,2])/norm[valid]
            broad = np.flatnonzero(valid & (nz <= math.cos(math.radians(75.0))+1e-12))
            for local_index in broad:
                points=triangle[local_index]; n=float(nz[local_index])
                zmin=float(points[:,2].min());zmax=float(points[:,2].max())
                cells=list(projected_cells(points[:,:2],origin,resolution,width,height))
                for name,degrees in CATEGORIES:
                    if n > math.cos(math.radians(degrees))+1e-12:
                        continue
                    result=results[name];result["triangles"]+=1
                    result["groups"][group]=result["groups"].get(group,0)+1
                    records=result["cells"]
                    for row,col in cells:
                        key=(row,col,group_id)
                        value=records.get(key)
                        if value is None:
                            records[key]=[1,zmin,zmax,n,n]
                        else:
                            value[0]+=1;value[1]=min(value[1],zmin);value[2]=max(value[2],zmax)
                            value[3]=min(value[3],n);value[4]=max(value[4],n)
        if primitive_index % 10 == 0:
            print(json.dumps({"primitive":primitive_index,"group":group,"triangles_processed":triangles_total,
                              "seconds":time.monotonic()-started}),flush=True)
    if minimum[0] < origin[0]-1e-5 or minimum[1] < origin[1]-1e-5 or maximum[0] > origin[0]+width*resolution+1e-5 or maximum[1] > origin[1]+height*resolution+1e-5:
        raise ValueError("Geometry lies outside original grid bounds")
    summaries=[]
    for name,degrees in CATEGORIES:
        target=out/name;target.mkdir()
        result=results[name];records=result["cells"]
        keys=sorted(records)
        indexes=np.asarray(keys,dtype="<i4").reshape(-1,3)
        stats=np.asarray([records[key] for key in keys],dtype="<f8").reshape(-1,5)
        occupied=np.zeros((height,width),dtype=bool)
        if len(indexes):
            occupied[indexes[:,0],indexes[:,1]]=True
        pixels=np.full((height,width),205,dtype=np.uint8)
        pixels[original_free]=254;pixels[occupied]=0
        pgm=target/"map.pgm"
        pgm.write_bytes(f"P5\n{width} {height}\n255\n".encode("ascii")+np.flipud(pixels).tobytes())
        # Byte-identical spatial declaration; only the raster role changes.
        shutil.copyfile(navigation/"map.yaml",target/"map.yaml")
        provenance=target/"surface-provenance.npz"
        np.savez_compressed(provenance,row=indexes[:,0],col=indexes[:,1],group_id=indexes[:,2],
            contributing_triangles=stats[:,0].astype("<u4"),z_min_m=stats[:,1],z_max_m=stats[:,2],
            abs_normal_z_min=stats[:,3],abs_normal_z_max=stats[:,4])
        metadata = dict(role="offline_geometry_candidate",status="generated_not_native_validated",
            map_id=info["map_id"],source_md5=info["source_md5"],**hashes,
            scene_geometry_sha256=nav_manifest["scene_geometry_sha256"],
            map_yaml_sha256=sha(target/"map.yaml"),raster_sha256=sha(pgm),
            surface_provenance_sha256=sha(provenance),exporter_sha256=sha(Path(__file__)),
            geometry_reader_sha256=sha(reader),source_step_sha256=descriptor_data["source_step_sha256"],
            source_to_sim_translation_m=translation.tolist(),coordinate_system=descriptor_data["coordinate_system"],units="m",
            width=width,height=height,resolution=resolution,resolution_float32=float(np.float32(resolution)),
            origin=info["origin"],geometry_bounds_m=[minimum.tolist(),maximum.tolist()],
            algorithm="Original CAD collision triangles; physical face normals; conservative actual XY triangle projection; existing free cells retained; other cells unknown.",
            normal_criterion={"minimum_surface_inclination_from_horizontal_deg":degrees,
                              "maximum_abs_normal_z":math.cos(math.radians(degrees)),
                              "justification":"Structural near-vertical surfaces; excludes the 42 degree traversable corrugation slope and horizontal support surfaces; fixed before scoring any scan."},
            height_filter={"mode":"none","source_geometry_z_range_m":[float(minimum[2]),float(maximum[2])],
                           "reason":"Recorded LaserScan has no original hit z; no unmeasured narrow slice is assumed. Actual per-cell contributing face z ranges are preserved."},
            rasterization={"grid_cells":"[origin+i*resolution, origin+(i+1)*resolution] closed conservative intersections",
                           "numerical_boundary_epsilon_m":1e-9,"geometry_translation_adjustment_m":[0.,0.,0.],
                           "amcl_model_note":"Score with unchanged upstream MAP_GXWX; no half-cell shift or evaluation-pose registration."},
            geometry_triangles_total=triangles_total,geometry_degenerate_triangles=degenerate,
            selected_triangles=result["triangles"],selected_triangles_by_group=result["groups"],groups=group_names,
            occupied_cells=int(occupied.sum()),free_cells=int((pixels==254).sum()),unknown_cells=int((pixels==205).sum()),
            occupied_over_prior_free_cells=int((occupied & original_free).sum()),
            cell_group_records=len(indexes),wall_seconds=time.monotonic()-started,
            evaluation_inputs_used=[],original_navigation_preserved=True,
            limits=["No native AMCL precision claim","Horizontal top surfaces and oblique ramps may still appear in the unchanged terrain-derived scan and are not represented by this candidate",
                    "Visibility/occlusion not encoded in 2D surface union","Per-cell z bounds are conservative contributing-triangle extrema",
                    "Comparison must preserve all exact recorded scan pairs and original AMCL likelihood semantics"])
        (target/"metadata.json").write_text(json.dumps(metadata,indent=2)+"\n",encoding="utf-8",newline="\n")
        summaries.append({k:metadata[k] for k in ["role","status","raster_sha256","selected_triangles","occupied_cells","free_cells","unknown_cells","occupied_over_prior_free_cells"]}|{"directory":str(target),"category":name})
    unchanged={rel:sha(Path(rel))==digest for rel,digest in baseline_hashes.items()}
    summary={"status":"generated_not_native_validated","production_inputs_unchanged":all(unchanged.values()),
             "input_checks":unchanged,"candidates":summaries,"wall_seconds":time.monotonic()-started}
    (out/"verification.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps(summary,indent=2),flush=True)
    if not summary["production_inputs_unchanged"]:
        raise RuntimeError("Production input changed during offline generation")

if __name__ == "__main__":
    main()
