"""Recover original landmesh position.w opacity without changing source files."""
import argparse
import collections
import contextlib
import datetime
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import numpy as np
from dagor_codec import configure_oodle
from dagor_vertex import TYPES, decode_vertices
from extract_wt_terrain import chunks

SOURCE_SHA = '427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def require(value, message):
    if not value:
        raise ValueError(message)


def material_id(spec):
    value = {key: spec[key] for key in ('shader', 'parameters', 'texture_slots')}
    return 'WT_'+hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--dae-root', type=Path, required=True)
    parser.add_argument('--oodle-library', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    land_dir = args.source_root.resolve()/'landmesh-01'
    land_path = land_dir/'landmesh.json'
    metadata = json.loads(land_path.read_text(encoding='utf-8-sig'))
    source_path = Path(metadata['source'])
    source = source_path.read_bytes()
    source_sha = hashlib.sha256(source).hexdigest()
    require(source_sha == metadata['sha256'] == SOURCE_SHA, 'original level identity differs')
    report = dict(schema='wt_landmesh_raw_opacity_verification_v1', status='running',
                  source_sha256=source_sha, source_file=str(source_path),
                  source_landmesh_sha256=sha(land_path), source_inputs=[], draws=[],
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  extraction_script_sha256=sha(Path(__file__)), source_modified=False)
    try:
        configure_oodle(args.oodle_library)
        sys.path.insert(0, str(args.dae_root.resolve()))
        with contextlib.redirect_stdout(io.StringIO()):
            from util.fileread import BinFile, readInt
            from util.decompression import CompressedData
            from parse.mesh import MatVData, ShaderMesh
            import util.log as log
            log.log = lambda *a, **k: None

            def block(file):
                size = readInt(file)
                require(size >> 30 == 0, 'unexpected compressed container block')
                return file.readBlock(size)

            payload = next(b for name, _, b in chunks(source) if name == 'lmap')
            file = BinFile(payload)
            magic, version, grid, cell, width, height, ox, oy, tile = struct.unpack('<4sIff4iI', file.read(36))
            require(magic == b'lndm' and version == 4, 'unsupported landmesh')
            offsets = struct.unpack('<4I', file.read(16)); file.seek(36+offsets[0])
            texture_count, material_count, vertex_data_count, header_size = struct.unpack('<4I', file.read(16))
            strings = file.read(readInt(file)); pointer, count = struct.unpack_from('<2I', strings)
            require(count == texture_count, 'texture table count differs')
            names = []
            for i in range(count):
                start = struct.unpack_from('<I', strings, pointer+4*i)[0]
                names.append(strings[start:strings.index(0, start)].decode('utf-8'))
            data = CompressedData(file).decompressToBin()
            mvd = MatVData(data, source_path.stem, texture_count, material_count, textures=names)
            mvd.headerSize = header_size & 0x3fffffff; mvd.computeData()
            outer = block(file); cells = []
            for i in range(width*height):
                c = block(outer); land = block(c); lods = []
                while land.tell() < land.getSize():
                    b = block(land); lods.append(ShaderMesh(b) if b.getSize() else None)
                categories = {'land': lods[0] if lods else None}
                for kind in ('decal', 'combined', 'patches'):
                    if c.tell() >= c.getSize():
                        categories[kind] = None
                    else:
                        b = block(c); categories[kind] = ShaderMesh(b) if b.getSize() else None
                require(c.tell() == c.getSize(), 'unconsumed original cell bytes')
                cells.append(categories)
            boxes = block(outer)
            bounds = np.frombuffer(boxes.read(width*height*24), dtype='<f4').reshape(-1, 2, 3).copy()
            boxes.read(width*height*4)
            require(outer.tell() == outer.getSize() and file.tell() == 36+offsets[1], 'container boundary differs')
            valid = np.all(bounds[:, 0] <= bounds[:, 1], axis=1)
            low = bounds[valid, 0].min(axis=0); high = bounds[valid, 1].max(axis=0)
            scale = np.array([cell/2, (high[1]-low[1])/2, cell/2])
            original_materials = [dict(shader=m.cls, parameters=m.par, texture_slots=list(m.getTextureSlots()))
                                  for m in mvd.getMaterials()]
        require([material_id(m) for m in original_materials] == [material_id(m) for m in metadata['materials']],
                'raw material identities differ from original extraction')
        cache = {}; vertices_all = []; triangles_all = []; materials_all = []; shorts_all = []; opacity_all = []
        vertex_base = 0; max_error = 0.0; near = []
        entries = [entry for entry in metadata['cells'] if entry['kind'] == 'decal']
        for entry in entries:
            cell_index = entry['cell']; mesh = cells[cell_index]['decal']
            require(mesh is not None and mesh.elems, 'original decal cell missing')
            offset = np.array([cell*(cell_index % width+ox+.5), (high[1]+low[1])/2,
                               cell*(cell_index//width+oy+.5)])
            vectors = []; triangles = []; materials = []; raw_short = []; opacity = []; local_base = 0
            for draw_index, e in enumerate(mesh.elems):
                if not e.numFace:
                    continue
                if e.vData not in cache:
                    with contextlib.redirect_stdout(io.StringIO()):
                        cache[e.vData] = mvd.getVertexData(e.vData, decode=False)
                vd = cache[e.vData]; global_data = vd.globalVertexData; stride = global_data.getVertexStride()
                faces = np.asarray(vd.getFaces()[e.startI//3:e.startI//3+e.numFace], dtype=np.int32)
                require(len(faces) == e.numFace, 'original draw face count differs')
                source_indices = np.unique(faces+e.baseVertex)
                raw = b''.join(vd.rawVertices[int(index)*stride:(int(index)+1)*stride] for index in source_indices)
                decoded = decode_vertices(raw, global_data.declarations, stride)[0]
                points = (np.asarray(decoded)*scale+offset).astype('<f4')
                channel_offset = 0; channels = []; selected = None
                for type_value, usage, index, stream in global_data.declarations:
                    kind = int(type_value) >> 16; dtype, dimensions = TYPES[kind]; item_size = np.dtype(dtype).itemsize
                    channels.append(dict(type_value=int(type_value), kind=kind, usage=int(usage), index=int(index),
                                         stream=int(stream), offset=channel_offset, components=dimensions, dtype=dtype))
                    if (usage, index) == (0, 0):
                        require(stream == 0 and kind == 10 and dimensions == 4,
                                'decal position must be original short4n')
                        selected = np.ndarray((len(source_indices), 4), dtype=dtype, buffer=raw,
                                              offset=channel_offset, strides=(stride, item_size))[:, 3].copy()
                    channel_offset += dimensions*item_size
                require(channel_offset == stride and selected is not None, 'incomplete raw position declaration')
                normalized = np.maximum(selected.astype(np.float32)/np.float32(32767.), np.float32(-1.))
                require(np.all(np.isfinite(normalized)) and np.all((normalized >= 0) & (normalized <= 1)),
                        'source opacity is outside expected range')
                lookup = {int(old): local_base+j for j, old in enumerate(source_indices)}
                triangles.extend([[lookup[int(index)+e.baseVertex] for index in face] for face in faces])
                materials.extend([int(e.mat)]*len(faces))
                vectors.append(points); raw_short.append(selected); opacity.append(normalized)
                local_base += len(points)
                report['draws'].append(dict(cell=cell_index, draw=draw_index, material=int(e.mat),
                    stable_material_id=material_id(metadata['materials'][e.mat]),
                    shader=metadata['materials'][e.mat]['shader'], vertices=len(points), triangles=len(faces),
                    stride=stride, channels=channels, selected_vertex_indices_sha256=hashlib.sha256(source_indices.tobytes()).hexdigest(),
                    selected_raw_bytes_sha256=hashlib.sha256(raw).hexdigest(),
                    raw_w_min=int(selected.min()), raw_w_max=int(selected.max())))
            points = np.concatenate(vectors); faces = np.asarray(triangles, dtype='<i4')
            ids = np.asarray(materials, dtype='<i4'); short_w = np.concatenate(raw_short); opacity_w = np.concatenate(opacity)
            path = land_dir/entry['file']; before = sha(path)
            with np.load(path, allow_pickle=False) as old:
                require(set(old.files) == {'vertices', 'uvs', 'triangles', 'material_ids'}, 'unexpected original NPZ channels')
                require(np.array_equal(points, old['vertices']), 'original NPZ position correspondence failed')
                require(np.array_equal(faces, old['triangles']), 'original NPZ face/index correspondence failed')
                require(np.array_equal(ids, old['material_ids']), 'original NPZ material correspondence failed')
                error = float(np.abs(points.astype(np.float64)-old['vertices']).max())
            require(sha(path) == before, 'source NPZ changed during read')
            report['source_inputs'].append(dict(file=entry['file'], path=str(path), sha256=before, cell=cell_index,
                                               vertices=len(points), triangles=len(faces), position_max_abs_error_m=error))
            max_error = max(max_error, error)
            if cell_index == 529:
                for face_index in (14888, 19952):
                    f = faces[face_index]; p = points[f].astype(np.float64); q = np.array([1028., 1020.])
                    weights = np.linalg.solve(np.column_stack((p[1, [0,2]]-p[0, [0,2]], p[2, [0,2]]-p[0, [0,2]])), q-p[0, [0,2]])
                    bary = np.array([1-weights.sum(), *weights])
                    near.append(dict(cell=cell_index, face=face_index, material=int(ids[face_index]),
                        material_id=material_id(metadata['materials'][ids[face_index]]),
                        raw_short_w=short_w[f].tolist(), opacity_w=opacity_w[f].tolist(),
                        barycentric=bary.tolist(), opacity_at_spawn=float(bary@opacity_w[f]),
                        source_y_at_spawn_m=float(bary@p[:,1])))
            vertices_all.append(points); triangles_all.append(faces+vertex_base); materials_all.append(ids)
            shorts_all.append(short_w); opacity_all.append(opacity_w); vertex_base += len(points)
        vertices = np.concatenate(vertices_all); triangles = np.concatenate(triangles_all)
        material_ids = np.concatenate(materials_all); raw_w = np.concatenate(shorts_all); opacity = np.concatenate(opacity_all)
        require(len(entries) == 99 and len(triangles) == 87864, 'unexpected Flanders source decal inventory')
        payload = output/'source-opacity.npz'
        np.savez_compressed(payload, vertices_dagor_m=vertices, triangles=triangles, material_ids=material_ids,
                            position_w_i16=raw_w, opacity_w=opacity)
        require(sha(source_path) == source_sha and sha(land_path) == report['source_landmesh_sha256'],
                'original source changed during extraction')
        report.update(status='passed', source_file_count=len(entries), source_vertices=len(vertices),
                      source_triangles=len(triangles), source_loop_count=int(triangles.size),
                      source_position_max_abs_error_m=max_error, payload_file=payload.name, payload_sha256=sha(payload),
                      opacity_min=float(opacity.min()), opacity_max=float(opacity.max()),
                      raw_w_unique_count=len(np.unique(raw_w)), zero_opacity_vertices=int(np.count_nonzero(opacity == 0)),
                      one_opacity_vertices=int(np.count_nonzero(opacity == 1)), spawn_faces=near,
                      original_input_hashes_unchanged=True,
                      source='short4n_position_w', normalization='max(int16_w / 32767.0, -1), float32')
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(output/'verification.json', report)
    print(json.dumps({key:report[key] for key in ('status','source_file_count','source_vertices','source_triangles',
        'source_position_max_abs_error_m','opacity_min','opacity_max','raw_w_unique_count','zero_opacity_vertices',
        'one_opacity_vertices','spawn_faces','payload_sha256')}))


if __name__ == '__main__':
    main()
