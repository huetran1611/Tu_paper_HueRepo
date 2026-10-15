#!/usr/bin/env python3
"""Build fixed OSRM paths and length-weighted Hanoi daily OD speed profiles."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import time
import unicodedata
import xml.etree.ElementTree as ET

import networkx as nx
import numpy as np
import requests
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / 'instance_hanoi/datasets/hanoi_traffic'
ORIGIN = (105.8206, 21.0017)
HOURS = tuple(range(6, 18))
DRIVABLE = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified',
            'residential', 'living_street', 'service', 'motorway_link', 'trunk_link',
            'primary_link', 'secondary_link', 'tertiary_link'}


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def project(lon, lat):
    return ((lon - ORIGIN[0]) * 111320 * math.cos(math.radians(ORIGIN[1])),
            (lat - ORIGIN[1]) * 111320)


def normalize(name):
    text = unicodedata.normalize('NFC', name).casefold().strip()
    for prefix in ('đường ', 'phố '):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return ' '.join(text.split())


def nodes_and_instances(root):
    nodes = {0: {'lon': ORIGIN[0], 'lat': ORIGIN[1]}}
    instances = {}
    for path in sorted(root.glob('set_*/nga_tu_so_10x10km_100_points_min500m.csv')):
        with path.open(encoding='utf-8-sig', newline='') as handle:
            rows = sorted(csv.DictReader(handle), key=lambda r: int(r['point_id']))
        ids = [0]
        for row in rows:
            key = int(row['source_point_id'])
            value = {'lon': float(row['lon']), 'lat': float(row['lat'])}
            if key in nodes and nodes[key] != value:
                raise ValueError(f'Conflicting source point {key}')
            nodes[key] = value
            ids.append(key)
        if len(ids) != 101 or len(set(ids)) != 101:
            raise ValueError(f'Expected 100 unique customers in {path}')
        instances[path.parent.name] = {'source_ids': ids, 'points_file': str(path), 'points_sha256': sha256(path)}
    if len(instances) != 20:
        raise ValueError(f'Expected 20 instances, found {len(instances)}')
    pairs = sorted({(i, j) for info in instances.values() for i in info['source_ids']
                    for j in info['source_ids'] if i != j})
    return nodes, instances, pairs


def read_map(path):
    nodes, ways = {}, {}
    for _, element in ET.iterparse(path, events=('end',)):
        if element.tag == 'node':
            nodes[int(element.attrib['id'])] = (float(element.attrib['lon']), float(element.attrib['lat']))
            element.clear()
        elif element.tag == 'way':
            tags = {t.attrib['k']: t.attrib['v'] for t in element.findall('tag')}
            refs = [int(n.attrib['ref']) for n in element.findall('nd')]
            if tags.get('highway') in DRIVABLE:
                ways[int(element.attrib['id'])] = {'tags': tags, 'nodes': refs}
            element.clear()
        elif element.tag == 'relation':
            element.clear()
    return nodes, ways


def fetch_map_extract(args):
    import osmium

    target = args.work / 'hanoi.osm'
    if target.exists():
        print(f'Reusing {target}', flush=True)
        return
    url = 'https://download.geofabrik.de/asia/vietnam-latest.osm.pbf'
    source = args.work / 'vietnam.osm.pbf'
    if not source.exists():
        temporary = source.with_suffix('.partial')
        with requests.get(url,stream=True,timeout=(20,120),
                          headers={'User-Agent':'HanoiResearchPathDataset/1.0'}) as response:
            response.raise_for_status()
            total = int(response.headers.get('Content-Length',0))
            count = 0
            with temporary.open('wb') as handle:
                for chunk in response.iter_content(1024*1024):
                    handle.write(chunk)
                    count += len(chunk)
                    if count//(25*1024*1024) != (count-len(chunk))//(25*1024*1024):
                        print(f'Downloaded OSM extract {count//1024//1024} MiB / {total//1024//1024} MiB',flush=True)
            if total and count != total:
                raise ValueError('Truncated Geofabrik download')
            temporary.replace(source)
    south,west,north,east = (20.86,105.68,21.14,105.97)
    selected = set()
    restrictions = 0
    print('Extracting complete Hanoi highway ways and restriction references',flush=True)
    with osmium.BackReferenceWriter(target,ref_src=source,remove_tags=False) as writer:
        for obj in osmium.FileProcessor(source).with_locations():
            if obj.is_way() and 'highway' in obj.tags:
                if any(n.location.valid() and west<=n.lon<=east and south<=n.lat<=north for n in obj.nodes):
                    writer.add_way(obj)
                    selected.add(obj.id)
            elif obj.is_relation() and obj.tags.get('type')=='restriction':
                if any(member.type=='w' and member.ref in selected for member in obj.members):
                    writer.add_relation(obj)
                    restrictions += 1
    dump(args.work/'osm_source.json',{'source_url':url, 'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),
         'source_sha256':sha256(source),'sha256':sha256(target),'bbox':[south,west,north,east],
         'selected_highway_ways':len(selected),'restriction_relations':restrictions,
         'extraction':'pyosmium BackReferenceWriter, complete referenced objects',
         'osm_attribution':'OpenStreetMap contributors; regional source Geofabrik'})
    print(f'Saved {target}: {len(selected)} highway ways and {restrictions} restrictions',flush=True)


def build_mapping(args):
    """Fix spatial mapping without inspecting any date's speed values."""
    nodes, ways = read_map(args.work / 'hanoi.osm')
    source = args.days / 'synthetic_manifest.json'
    metadata = json.loads(source.read_text())
    first = args.days / metadata['profiles'][0]['dates'][0]['file']
    with first.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    keys = [(normalize(r['Ten_Duong']), normalize(r['Loai_Duong'])) for r in rows]
    key_index = {key: i for i, key in enumerate(keys)}
    names = defaultdict(set)
    for i, (name, _) in enumerate(keys):
        names[name].add(i)
    way_rows = defaultdict(set)
    with (DATA / 'roads_by_district.csv').open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            index = key_index.get((normalize(row['road']), normalize(row['district'])))
            if index is not None:
                for way in row['osm_way_ids'].split():
                    way_rows[int(way)].add(index)
    cached = json.loads((DATA / 'osm_20x20km_named_highways.json').read_text())['elements']
    anchors, anchor_rows, anchor_classes = [], [], []
    for way in cached:
        if way.get('tags',{}).get('highway') not in DRIVABLE:
            continue
        candidate_rows = way_rows.get(int(way['id']), set())
        if not candidate_rows:
            continue
        geometry = way.get('geometry', [])
        for a, b in zip(geometry, geometry[1:]):
            point = project((a['lon'] + b['lon']) / 2, (a['lat'] + b['lat']) / 2)
            for index in sorted(candidate_rows):
                anchors.append(point)
                anchor_rows.append(index)
                anchor_classes.append(way.get('tags', {}).get('highway', 'unknown'))
    if not anchors:
        raise ValueError('No spatial reference anchors for traffic roads')
    anchors = np.array(anchors)
    anchor_rows = np.array(anchor_rows)
    row_anchors = {row: np.flatnonzero(anchor_rows == row) for row in set(anchor_rows.tolist())}
    global_tree = cKDTree(anchors)
    class_trees = {}
    for cls in set(anchor_classes):
        mask = np.flatnonzero(np.array(anchor_classes) == cls)
        class_trees[cls] = (cKDTree(anchors[mask]), mask)
    mapping, edge_way = {}, {}
    for way_id, way in ways.items():
        coords = [nodes[n] for n in way['nodes'] if n in nodes]
        if len(coords) < 2:
            continue
        middle = coords[len(coords) // 2]
        point = project(*middle)
        candidates = set(way_rows.get(way_id) or names.get(normalize(way['tags'].get('name', ''))) or ())
        candidates.intersection_update(row_anchors)
        if candidates:
            indices = np.concatenate([row_anchors[row] for row in sorted(candidates) if row in row_anchors])
            if not len(indices):
                raise ValueError('Named road has no reference geometry')
            nearest = indices[np.argmin(np.sum((anchors[indices] - point) ** 2, axis=1))]
            row = int(anchor_rows[nearest])
            method = 'osm_way_id' if way_id in way_rows else 'road_name_spatial'
            distance = float(np.linalg.norm(anchors[nearest] - point))
        else:
            cls = way['tags']['highway']
            if cls in class_trees:
                tree, indices = class_trees[cls]
                distance, local = tree.query(point)
                nearest = indices[int(local)]
                method = 'estimated_nearest_same_highway'
            else:
                distance, nearest = global_tree.query(point)
                method = 'estimated_nearest_road'
            row = int(anchor_rows[int(nearest)])
        mapping[way_id] = {'road_row': row, 'method': method, 'reference_distance_m': float(distance),
                           'osm_name': way['tags'].get('name', ''), 'highway': way['tags']['highway']}
        for a, b in zip(way['nodes'], way['nodes'][1:]):
            edge_way.setdefault((min(a, b), max(a, b)), way_id)
    dump(args.work / 'road_mapping.json', {'traffic_road_keys': keys, 'mapping': mapping,
         'counts': dict(Counter(item['method'] for item in mapping.values())),
         'direction_assumption': 'same speed for both directions of a physical road portion',
         'fallback': 'fixed spatial nearest same highway class, then nearest reference road; no speed values fitted',
         'inputs': {'osm_sha256': sha256(args.work / 'hanoi.osm'),
                    'roads_by_district_sha256': sha256(DATA / 'roads_by_district.csv'),
                    'osm_anchor_cache_sha256': sha256(DATA / 'osm_20x20km_named_highways.json')}})
    np.savez_compressed(args.work / 'reference_anchors.npz', xy=anchors, road_rows=anchor_rows)
    # Integer node-pair references retain topology for OSRM annotation matching.
    database = sqlite3.connect(args.work / 'paths.sqlite')
    database.execute('CREATE TABLE IF NOT EXISTS edge_way (a INTEGER,b INTEGER,way INTEGER,PRIMARY KEY(a,b))')
    database.executemany('INSERT OR REPLACE INTO edge_way VALUES (?,?,?)', ((a,b,w) for (a,b),w in edge_way.items()))
    database.commit()
    database.close()
    print(f'Mapped {len(mapping)} ways and {len(edge_way)} physical edges', flush=True)


def fetch_paths(args):
    nodes, instances, pairs = nodes_and_instances(args.instances)
    metadata_path = args.work / 'path_inputs.json'
    current_inputs = {'nodes': nodes, 'instances': instances, 'pairs': len(pairs),
                      'osrm_url': args.osrm_url, 'osm_sha256': sha256(args.work / 'hanoi.osm'),
                      'batch_legs':args.batch_legs, 'networkx_version':nx.__version__,
                      'continue_straight':False}
    # JSON normalizes integer dictionary keys, so compare serialized representations.
    normalized = json.loads(json.dumps(current_inputs))
    if metadata_path.exists() and json.loads(metadata_path.read_text()) != normalized:
        raise ValueError('Path cache belongs to different inputs or routing map')
    dump(metadata_path, current_inputs)
    snap_path = args.work / 'snapped_nodes.json'
    if snap_path.exists():
        snapped = {int(k): v for k, v in json.loads(snap_path.read_text()).items()}
    else:
        snapped = {}
        session = requests.Session()
        occupied = set()
        for key, point in sorted(nodes.items()):
            url = f"{args.osrm_url}/nearest/v1/driving/{point['lon']},{point['lat']}"
            response = session.get(url, params={'number': 10}, timeout=30)
            response.raise_for_status()
            alternatives = response.json()['waypoints']
            selected = next(((rank,w) for rank,w in enumerate(alternatives)
                             if tuple(w['location']) not in occupied), None)
            if selected is None:
                raise ValueError(f'No unique drivable access point for source node {key}')
            rank, waypoint = selected
            waypoint['nearest_candidate_rank'] = rank
            occupied.add(tuple(waypoint['location']))
            snapped[key] = waypoint
        dump(snap_path, snapped)
    # Nearest can select an isolated component; require a drivable depot round trip.
    if not all(v.get('depot_roundtrip_verified') for v in snapped.values()):
        def reachable(item):
            key, candidate = item
            sequence = [snapped[0],candidate,snapped[0]]
            coords = ';'.join(','.join(map(str,p['location'])) for p in sequence)
            params = {'overview':'false','continue_straight':'false',
                      'hints':';'.join(p['hint'] for p in sequence)}
            r = requests.get(f'{args.osrm_url}/route/v1/driving/{coords}',params=params,timeout=30)
            return key,r.status_code==200 and r.json().get('code')=='Ok'
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            checks = list(pool.map(reachable, sorted(snapped.items())))
        bad = [key for key,ok in checks if not ok]
        print(f'Depot reachability: {len(checks)-len(bad)}/{len(checks)} access points; resolving {len(bad)}',flush=True)
        for key in bad:
            point = nodes[key]
            r = requests.get(f"{args.osrm_url}/nearest/v1/driving/{point['lon']},{point['lat']}",params={'number':30},timeout=30)
            r.raise_for_status()
            occupied = {tuple(v['location']) for k,v in snapped.items() if k!=key}
            for rank,candidate in enumerate(r.json()['waypoints']):
                if tuple(candidate['location']) in occupied:
                    continue
                if reachable((key,candidate))[1]:
                    candidate['nearest_candidate_rank']=rank
                    snapped[key]=candidate
                    break
            else:
                raise ValueError(f'No depot-reachable access point for {key}')
        for item in snapped.values():
            item['depot_roundtrip_verified']=True
        dump(snap_path,snapped)
        if bad:
            c=sqlite3.connect(args.work/'paths.sqlite')
            c.execute('CREATE TABLE IF NOT EXISTS paths (i INTEGER,j INTEGER,data TEXT,PRIMARY KEY(i,j))')
            c.execute('CREATE TABLE IF NOT EXISTS batches (batch INTEGER PRIMARY KEY)')
            c.execute('DELETE FROM paths')
            c.execute('DELETE FROM batches')
            c.commit()
            c.close()
    database = sqlite3.connect(args.work / 'paths.sqlite')
    database.execute('CREATE TABLE IF NOT EXISTS paths (i INTEGER,j INTEGER,data TEXT,PRIMARY KEY(i,j))')
    database.execute('CREATE TABLE IF NOT EXISTS batches (batch INTEGER PRIMARY KEY)')
    done = {row[0] for row in database.execute('SELECT batch FROM batches')}
    graph = nx.DiGraph()
    graph.add_edges_from(pairs)
    if not nx.is_eulerian(graph):
        raise ValueError('Expected balanced connected directed pair graph')
    circuit = list(nx.eulerian_circuit(graph, source=0))
    chunks = [circuit[start:start + args.batch_legs] for start in range(0, len(circuit), args.batch_legs)]
    def fetch(item):
        index, edges = item
        ids = [edges[0][0]] + [edge[1] for edge in edges]
        if any(edges[k][1] != edges[k+1][0] for k in range(len(edges)-1)):
            raise ValueError('Non-contiguous route batch')
        coords = ';'.join(','.join(map(str, snapped[n]['location'])) for n in ids)
        parameters = {'steps': 'false', 'overview': 'false', 'annotations': 'nodes,distance',
                      'continue_straight': 'false', 'hints': ';'.join(snapped[n]['hint'] for n in ids)}
        for attempt in range(3):
            try:
                response = requests.get(f'{args.osrm_url}/route/v1/driving/{coords}', params=parameters, timeout=180)
                payload = response.json()
                if response.status_code != 200 or payload.get('code') != 'Ok':
                    raise ValueError(f'Batch {index}: {payload.get("code")}: {payload.get("message")}')
                legs = payload['routes'][0]['legs']
                if len(legs) != len(edges):
                    raise ValueError('OSRM leg count mismatch')
                return index, [(i, j, json.dumps(leg, separators=(',', ':'))) for (i,j),leg in zip(edges,legs)]
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    work = [(i, edges) for i, edges in enumerate(chunks) if i not in done]
    print(f'{len(pairs)} unique OD pairs, {len(chunks)} batches, {len(work)} uncached', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, item) for item in work]
        try:
            for count, future in enumerate(as_completed(futures), 1):
                index, records = future.result()
                database.executemany('INSERT OR REPLACE INTO paths VALUES (?,?,?)', records)
                database.execute('INSERT OR REPLACE INTO batches VALUES (?)', (index,))
                database.commit()
                if count % 25 == 0 or count == len(work):
                    print(f'Cached {count}/{len(work)} batches', flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    count = database.execute('SELECT COUNT(*) FROM paths').fetchone()[0]
    database.close()
    if count != len(pairs):
        raise ValueError(f'Incomplete path count: {count}/{len(pairs)}')
    print(f'All {count} unique directed paths cached', flush=True)


def path_portions(leg, edge_way, mapping, node_coords, reference_tree, reference_rows):
    annotation = leg['annotation']
    node_ids = [int(n) for n in annotation['nodes']]
    lengths = annotation['distance']
    if len(node_ids) != len(lengths) + 1:
        raise ValueError('Unexpected OSRM annotation node/distance alignment')
    way_ids = [edge_way.get((min(a,b), max(a,b))) for a,b in zip(node_ids,node_ids[1:])]
    portions = defaultdict(float)
    estimated = boundary_inferred = name_matched = 0.0
    for index, length in enumerate(lengths):
        if length < 0 or not math.isfinite(length):
            raise ValueError('Invalid annotation length')
        if length <= 1e-9:
            continue
        way = way_ids[index]
        if way is None and node_ids[index] == node_ids[index+1]:
            adjacent = (way_ids[index+1] if index+1 < len(way_ids) else None,
                        way_ids[index-1] if index > 0 else None)
            way = next((w for w in adjacent if w is not None), None)
            if way is not None:
                boundary_inferred += length
        if way is not None and str(way) in mapping:
            assignment = mapping[str(way)]
            road = assignment['road_row']
            method = assignment['method']
        else:
            points = [node_coords[n] for n in node_ids[index:index+2] if n in node_coords]
            if not points:
                raise ValueError('Cannot locate unmapped OSRM path portion')
            midpoint = (statistics_mean(p[0] for p in points), statistics_mean(p[1] for p in points))
            _, nearest = reference_tree.query(project(*midpoint))
            road = int(reference_rows[int(nearest)])
            method = 'estimated_unmapped_annotation_edge'
        portions[int(road)] += length
        if method.startswith('estimated'):
            estimated += length
        elif method == 'road_name_spatial':
            name_matched += length
    distance = sum(portions.values())
    if abs(distance - sum(lengths)) > 1e-6 * max(1, distance):
        raise ValueError('Path portion lengths do not sum to path distance')
    return dict(portions), distance, estimated, boundary_inferred, name_matched


def statistics_mean(values):
    values = list(values)
    return sum(values) / len(values)


def build_exposure(args):
    nodes, instances, pairs = nodes_and_instances(args.instances)
    metadata = json.loads((args.work / 'road_mapping.json').read_text())
    node_coords, _ = read_map(args.work / 'hanoi.osm')
    with np.load(args.work / 'reference_anchors.npz') as archive:
        anchors = {key:archive[key] for key in archive.files}
    tree = cKDTree(anchors['xy'])
    connection = sqlite3.connect(args.work / 'paths.sqlite')
    edge_way = {(a,b): w for a,b,w in connection.execute('SELECT a,b,way FROM edge_way')}
    cached_count = connection.execute('SELECT COUNT(*) FROM paths').fetchone()[0]
    if cached_count != len(pairs):
        raise ValueError(f'Expected {len(pairs)} paths, got {cached_count}')
    row_indices, columns, values, distances, estimated, boundaries, named = [], [], [], [], [], [], []
    for index, (i,j,text) in enumerate(connection.execute('SELECT i,j,data FROM paths ORDER BY i,j')):
        if (i,j) != pairs[index]:
            raise ValueError('Unexpected cached OD pair')
        leg = json.loads(text)
        portions, distance, fallback, boundary, name_length = path_portions(
            leg, edge_way, metadata['mapping'], node_coords, tree, anchors['road_rows'])
        for road, length in portions.items():
            row_indices.append(index)
            columns.append(road)
            values.append(length)
        distances.append(distance)
        estimated.append(fallback)
        boundaries.append(boundary)
        named.append(name_length)
        if (index+1) % 25000 == 0:
            print(f'Aggregated road lengths for {index+1}/{len(pairs)} paths', flush=True)
    connection.close()
    exposure = csr_matrix((values, (row_indices, columns)), shape=(len(pairs),len(metadata['traffic_road_keys'])))
    from scipy.sparse import save_npz
    save_npz(args.work / 'path_road_lengths.npz', exposure)
    np.savez_compressed(args.work / 'path_index.npz', pairs=np.array(pairs,dtype=np.int32),
                        distance_m=np.array(distances), estimated_length_m=np.array(estimated),
                        boundary_inferred_length_m=np.array(boundaries), name_matched_length_m=np.array(named))
    dump(args.work / 'exposure_summary.json', {'paths': len(pairs), 'positive_paths': int(np.count_nonzero(distances)),
         'zero_distance_pairs': [pairs[k] for k,d in enumerate(distances) if d <= 1e-9],
         'total_path_length_m': sum(distances), 'total_estimated_length_m': sum(estimated),
         'length_weighted_estimated_share_pct': 100 * sum(estimated) / sum(distances),
         'max_path_estimated_share_pct': float(np.max(np.clip(np.divide(estimated, distances, out=np.zeros(len(pairs)), where=np.array(distances)>0),0,1))*100),
         'mapping_sha256': sha256(args.work / 'road_mapping.json'), 'paths_sqlite_sha256': sha256(args.work / 'paths.sqlite')})
    print(json.dumps(json.loads((args.work / 'exposure_summary.json').read_text())), flush=True)


def daily_road_speeds(args, metadata):
    mapping = json.loads((args.work / 'road_mapping.json').read_text())
    expected = [tuple(k) for k in mapping['traffic_road_keys']]
    output = []
    for profile in metadata['profiles']:
        for item in profile['dates']:
            path = args.days / item['file']
            if sha256(path) != item['sha256']:
                raise ValueError(f'Synthetic source changed: {path}')
            with path.open(encoding='utf-8-sig',newline='') as handle:
                rows = list(csv.DictReader(handle))
            lookup = {(normalize(r['Ten_Duong']), normalize(r['Loai_Duong'])): r for r in rows}
            array = np.array([[float(lookup[key][f'{h}h']) for h in HOURS] for key in expected])
            if not np.all(np.isfinite(array) & (array > 0)):
                raise ValueError('Invalid source road speed')
            output.append((profile['day_type'], item['scenario_date_label'], array, item))
    return output


def training_profiles(daily):
    """Fixed length-weighted harmonic aggregation commutes in reciprocal speed."""
    inverse = 1 / daily
    folds = []
    for fold in range(4):
        test = {fold,4+fold,8+fold}
        train = [i for i in range(12) if i not in test]
        p0 = 1 / np.mean(inverse[train],axis=(0,3))
        p0 = np.repeat(p0[:,:,None],12,axis=2)
        profiles = [p0,1 / np.mean(inverse[train],axis=0)]
        for group in range(3):
            same_type = [i for i in range(group*4,group*4+4) if i not in test]
            profiles.append(1 / np.mean(inverse[same_type],axis=0))
        folds.append(profiles)
    return np.array(folds)


def export_profiles(args):
    from scipy.sparse import load_npz
    nodes, instances, pairs = nodes_and_instances(args.instances)
    matrix = load_npz(args.work / 'path_road_lengths.npz')
    with np.load(args.work / 'path_index.npz') as archive:
        index = {key:archive[key] for key in archive.files}
    if not np.array_equal(index['pairs'],np.array(pairs)):
        raise ValueError('Path indices differ from instances')
    distance = index['distance_m']
    if np.any(distance <= 0):
        raise ValueError('Zero-distance distinct-point paths require access-point resolution before export')
    metadata = json.loads((args.days / 'synthetic_manifest.json').read_text())
    daily = daily_road_speeds(args, metadata)
    # Length/speed is proportional to time. Keep this full-precision representation for diagnostics.
    all_speeds = []
    for group, day, speeds, source in daily:
        travel = matrix @ (1 / speeds)
        effective = distance[:,None] / travel
        if not np.all(np.isfinite(effective) & (effective > 0)):
            raise ValueError('Invalid effective path speeds')
        all_speeds.append(effective)
    all_speeds = np.array(all_speeds)
    pair_index = {pair:k for k,pair in enumerate(pairs)}
    args.output.mkdir(parents=True,exist_ok=True)
    snapshots = {int(k):v for k,v in json.loads((args.work / 'snapped_nodes.json').read_text()).items()}
    outputs = []
    for name, info in instances.items():
        directory = args.output / name
        directory.mkdir(parents=True,exist_ok=True)
        original_directory = args.instances / name
        stem = f'hanoi_10x10_100_{name}_weekday'
        shutil.copy2(original_directory / f'{stem}.txt', directory / 'instance.txt')
        shutil.copy2(original_directory / f'{stem}.drone_distance_m.txt', directory / 'drone_distance_m.txt')
        shutil.copy2(Path(info['points_file']), directory / 'customer_points.csv')
        ids = info['source_ids']
        ii,jj = np.where(~np.eye(101,dtype=bool))
        positions = np.array([pair_index[(ids[i],ids[j])] for i,j in zip(ii,jj)])
        d = np.zeros((101,101))
        d[ii,jj] = distance[positions]
        with (directory / 'truck_distance_m.txt').open('w') as handle:
            handle.write('# i j distance_m; fixed local OSRM car paths; node 0 is depot\n')
            for i in range(101):
                for j in range(101):
                    handle.write(f'{i} {j} {d[i,j]:.6f}\n')
        with (directory / 'path_coverage.csv').open('w',newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['i','j','source_i','source_j','distance_m','estimated_length_m',
                             'estimated_share_pct','boundary_inferred_length_m','name_matched_length_m','global_path_index'])
            for i,j,k in zip(ii,jj,positions):
                writer.writerow([i,j,ids[i],ids[j],distance[k],index['estimated_length_m'][k],
                                 index['estimated_length_m'][k]/distance[k]*100,
                                 index['boundary_inferred_length_m'][k],index['name_matched_length_m'][k],k])
        node_report = [{'local_id': i, 'source_id': key, 'original': nodes[key],
                        'snapped_lonlat': snapshots[key]['location'], 'access_snap_distance_m': snapshots[key]['distance'],
                        'nearest_candidate_rank':snapshots[key].get('nearest_candidate_rank',0)}
                       for i,key in enumerate(ids)]
        dump(directory / 'access_points.json', node_report)
        # A positive diagonal sentinel is unused; distinct pairs always use their real path speed.
        array = np.ones((12,101,101,12),dtype=np.float64)
        for day_index,(group,day,_,_) in enumerate(daily):
            array[day_index,ii,jj,:] = all_speeds[day_index,positions,:]
            destination = directory / group / f'{day}.v_ijl_kph.txt'
            destination.parent.mkdir(exist_ok=True)
            with destination.open('w') as handle:
                handle.write('# i j hour speed_kph; one-decimal export; 06:00..18:00 exclusive; diagonal sentinel=1.0\n')
                for h_index,hour in enumerate(HOURS):
                    for i in range(101):
                        for j in range(101):
                            handle.write(f'{i} {j} {hour} {array[day_index,i,j,h_index]:.1f}\n')
        np.savez_compressed(directory / 'daily_path_speeds_full_precision.npz',
                            speed_kph=array, distance_m=d, hours=np.array(HOURS),
                            dates=np.array([v[1] for v in daily]), source_ids=np.array(ids))
        np.savez_compressed(directory / 'training_profiles_full_precision.npz',
                            speed_kph=training_profiles(array),
                            policies=np.array(['P0','P1','P2_monday','P2_saturday','P2_sunday']),
                            folds=np.arange(1,5), hours=np.array(HOURS))
        original = np.zeros((101,101))
        original_path = args.instances / name / f'hanoi_10x10_100_{name}_weekday.truck_distance_m.txt'
        with original_path.open() as handle:
            for line in handle:
                if not line.strip() or line.startswith('#'):
                    continue
                i,j,value = line.split()
                original[int(i),int(j)] = float(value)
        original_values = original[ii,jj]
        differences = np.abs(d[ii,jj]-original_values)
        outputs.append({'instance':name, 'source_points_sha256':info['points_sha256'],
                        'scheduled_daily_profiles':12,'off_diagonal_paths':10100,
                        'total_path_length_m':float(np.sum(distance[positions])),
                        'total_estimated_path_length_m':float(np.sum(index['estimated_length_m'][positions])),
                        'max_access_snap_m':max(p['access_snap_distance_m'] for p in node_report),
                        'customers_with_access_snap_over_50m':sum(p['access_snap_distance_m']>50 for p in node_report[1:]),
                        'mean_absolute_distance_change_from_old_m':float(np.mean(differences)),
                        'mean_absolute_distance_change_from_old_pct':float(np.mean(differences/np.maximum(original_values,1))*100),
                        'mean_estimated_path_length_share_pct':float(np.mean(index['estimated_length_m'][positions]/distance[positions])*100)})
        print(f'Exported {name}: 12 profiles and matching path distances',flush=True)
    manifest = {'schema':1,'synthetic':True,'direction_assumption':'same road speed in both directions; paths/distances remain directed',
                'formula':'v_ijh = sum(length_a) / sum(length_a / speed_ah)',
                'hours':list(HOURS),'intervals':'[06,07),...,[17,18); 18h source column is not an extra interval',
                'daily_inputs_sha256':sha256(args.days/'synthetic_manifest.json'),
                'path_mapping_sha256':sha256(args.work/'road_mapping.json'),
                'full_precision_for_diagnostics':'daily_path_speeds_full_precision.npz',
                'text_precision_decimal_places':1,'diagonal_speed_sentinel_kph':1.0,
                'routing':'OSRM car.lua v5.27.1 on the saved OSM extract; nearest drivable access-point snapping',
                'limitations':['Spatial nearest-road speed imputation is reported, not observed traffic.',
                               'Customer coordinates are retained; truck road access uses recorded snapped proxies.',
                               'Distances are newly computed for these paths; do not mix with old distance matrices.',
                               'No intra-road travel or service time is added to OD speeds.',
                               'For P0/P1/P2, aggregate training dates only, with the same fixed road-length mapping.'],
                'full_dataset_estimated_length_share_pct':100*sum(v['total_estimated_path_length_m'] for v in outputs)/sum(v['total_path_length_m'] for v in outputs),
                'instances':outputs}
    dump(args.output/'manifest.json',manifest)
    with (args.output/'coverage_summary.csv').open('w',newline='') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(outputs[0]))
        writer.writeheader()
        writer.writerows(outputs)
    dump(args.output/'fold_map.json', [{'fold':i+1, 'monday':metadata['profiles'][0]['dates'][i]['scenario_date_label'],
         'saturday':metadata['profiles'][1]['dates'][i]['scenario_date_label'],
         'sunday':metadata['profiles'][2]['dates'][i]['scenario_date_label']} for i in range(4)])
    print(f'Export complete: {args.output}',flush=True)


def verify(args):
    from scipy.sparse import load_npz
    _,instances,pairs = nodes_and_instances(args.instances)
    mapping = load_npz(args.work/'path_road_lengths.npz')
    with np.load(args.work/'path_index.npz') as archive:
        index = {key:archive[key] for key in archive.files}
    metadata = json.loads((args.days/'synthetic_manifest.json').read_text())
    daily = daily_road_speeds(args,metadata)
    positions = {pair:k for k,pair in enumerate(pairs)}
    ii,jj = np.where(~np.eye(101,dtype=bool))
    reports = []
    for name,info in instances.items():
        directory = args.output/name
        with np.load(directory/'daily_path_speeds_full_precision.npz') as archive:
            speeds = archive['speed_kph']
            distances = archive['distance_m']
            if speeds.shape != (12,101,101,12) or np.any(~np.isfinite(speeds)) or np.any(speeds<=0):
                raise ValueError(f'Invalid full-precision matrix for {name}')
        rows = np.array([positions[(info['source_ids'][i],info['source_ids'][j])] for i,j in zip(ii,jj)])
        exposure = mapping[rows]
        if not np.allclose(distances[ii,jj],np.asarray(exposure.sum(axis=1)).ravel(),atol=1e-6,rtol=0):
            raise ValueError(f'Path lengths mismatch for {name}')
        with np.load(directory/'training_profiles_full_precision.npz') as archive:
            if not np.allclose(archive['speed_kph'],training_profiles(speeds),atol=1e-12,rtol=0):
                raise ValueError(f'Training profiles mismatch for {name}')
        max_error = max_rounding = 0.
        for day_index,(group,day,road_speeds,_) in enumerate(daily):
            expected = distances[ii,jj,None] / (exposure @ (1 / road_speeds))
            error = float(np.max(np.abs(expected-speeds[day_index,ii,jj,:])))
            max_error = max(max_error,error)
            path = directory/group/f'{day}.v_ijl_kph.txt'
            text = np.loadtxt(path)
            if text.shape != (122412,4) or np.any(text[:,3]<=0):
                raise ValueError(f'Incomplete text profile {path}')
            ti,tj,th = text[:,:3].astype(int).T
            expected_order = np.arange(122412)
            if not np.array_equal((th-6)*10201+ti*101+tj,expected_order):
                raise ValueError(f'Duplicate or unordered keys in {path}')
            rounding = float(np.max(np.abs(text[:,3]-speeds[day_index,ti,tj,th-6])))
            max_rounding = max(max_rounding,rounding)
            if not np.allclose(text[:,3]*10,np.rint(text[:,3]*10),atol=1e-9,rtol=0):
                raise ValueError('Text profile is not rounded to one decimal')
        if max_error>1e-10 or max_rounding>0.05000001:
            raise ValueError(f'Formula or rounding error for {name}')
        reports.append({'instance':name,'daily_files':12,'max_harmonic_formula_error_kph':max_error,
                        'max_text_rounding_error_kph':max_rounding,'training_profiles_verified':True})
        print(f'Verified {name}: all 12 text files, full precision, distances and training profiles',flush=True)
    report = {'instances':len(reports),'daily_files':sum(r['daily_files'] for r in reports),
              'text_speed_records':len(reports)*12*122412,'all_passed':True,'checks':reports}
    dump(args.output/'validation.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='checks'}),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=DATA / 'Data_9.2026' / 'path_network')
    parser.add_argument('--days', type=Path, default=DATA / 'Data_9.2026')
    parser.add_argument('--instances', type=Path, default=DATA / 'Hanoi_selected_20')
    parser.add_argument('--output', type=Path, default=DATA / 'Data_9.2026' / 'instances')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('fetch-map').set_defaults(func=fetch_map_extract)
    sub.add_parser('map-roads').set_defaults(func=build_mapping)
    sub.add_parser('build-exposure').set_defaults(func=build_exposure)
    sub.add_parser('export').set_defaults(func=export_profiles)
    sub.add_parser('verify').set_defaults(func=verify)
    p = sub.add_parser('fetch-paths')
    p.add_argument('--osrm-url', default='http://127.0.0.1:5057')
    p.add_argument('--batch-legs', type=int, default=199)
    p.add_argument('--workers', type=int, default=4)
    p.set_defaults(func=fetch_paths)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == '__main__':
    main()
