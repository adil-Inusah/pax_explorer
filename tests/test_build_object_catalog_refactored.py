from datetime import datetime, timezone
from pathlib import Path
import scripts.build_object_catalog as module

def write(p,x): module.write_json(p,x)
def base(tmp_path):
 c=tmp_path/"current";c.mkdir();
 write(c/"graph_manifest.json",{"status":"COMPLETE","published_current":True,"snapshot_id":"g1"})
 for n in ("graph_nodes.json","graph_relationships.json","graph_provenance.json","external_data_sources.json","process_data_sources.json"): write(c/n,[])
 return c
def build(tmp_path): return module.build_object_catalog(current_root=tmp_path/"current",snapshot_root=tmp_path/"snapshots",review_root=tmp_path/"review",timestamp=datetime(2026,9,25,21,0,tzinfo=timezone.utc))

def test_view_reference_uses_declaration_cube_plus_view(tmp_path):
 c=base(tmp_path); sa="data-source::TM1_CUBE_VIEW::a"; sb="data-source::TM1_CUBE_VIEW::b"
 write(c/"graph_nodes.json",[{"node_id":"process::A","node_type":"PROCESS","object_name":"A"},{"node_id":"process::B","node_type":"PROCESS","object_name":"B"},{"node_id":sa,"node_type":"EXTERNAL_DATA_SOURCE"},{"node_id":sb,"node_type":"EXTERNAL_DATA_SOURCE"}])
 write(c/"external_data_sources.json",[{"node_id":sa},{"node_id":sb}])
 write(c/"process_data_sources.json",[{"process_name":"A","process_id":"process::A","configured_data_source_id":"configured-source::a","source_id":sa,"shared_source_id":sa,"source_type":"TM1_CUBE_VIEW","effective_cube_name":"Cube A","view_name":"Temp"},{"process_name":"B","process_id":"process::B","configured_data_source_id":"configured-source::b","source_id":sb,"shared_source_id":sb,"source_type":"TM1_CUBE_VIEW","effective_cube_name":"Cube B","view_name":"Temp"}])
 assert build(tmp_path)["status"]=="COMPLETE"
 refs=[x for x in module.read_json(c/"object_catalog.json") if x["object_type"]=="TM1_VIEW_REFERENCE"]
 assert {tuple(x["identity_components"]) for x in refs}=={("TM1_VIEW_REFERENCE","cube a","temp"),("TM1_VIEW_REFERENCE","cube b","temp")}

def test_same_view_tuple_is_shared_by_unique_configurations(tmp_path):
 c=base(tmp_path); s="data-source::TM1_CUBE_VIEW::a"
 write(c/"graph_nodes.json",[{"node_id":"process::A","node_type":"PROCESS","object_name":"A"},{"node_id":"process::B","node_type":"PROCESS","object_name":"B"},{"node_id":s,"node_type":"EXTERNAL_DATA_SOURCE"}]);write(c/"external_data_sources.json",[{"node_id":s}])
 write(c/"process_data_sources.json",[{"process_name":"A","process_id":"process::A","configured_data_source_id":"configured-source::a","shared_source_id":s,"source_type":"TM1_CUBE_VIEW","effective_cube_name":"Cube A","view_name":"Temp"},{"process_name":"B","process_id":"process::B","configured_data_source_id":"configured-source::b","shared_source_id":s,"source_type":"TM1_CUBE_VIEW","effective_cube_name":"Cube A","view_name":"Temp"}])
 assert build(tmp_path)["status"]=="COMPLETE"
 objs=module.read_json(c/"object_catalog.json"); rels=module.read_json(c/"object_relationships.json")
 assert len([x for x in objs if x["object_type"]=="TM1_VIEW_REFERENCE"])==1
 assert len([x for x in rels if x["relationship_type"]=="REFERENCES_TM1_VIEW"])==2

def test_subset_reference_includes_hierarchy(tmp_path):
 c=base(tmp_path); sa="data-source::TM1_DIMENSION_SUBSET::a";sb="data-source::TM1_DIMENSION_SUBSET::b"
 write(c/"graph_nodes.json",[{"node_id":"process::A","node_type":"PROCESS","object_name":"A"},{"node_id":"process::B","node_type":"PROCESS","object_name":"B"},{"node_id":sa,"node_type":"EXTERNAL_DATA_SOURCE"},{"node_id":sb,"node_type":"EXTERNAL_DATA_SOURCE"}]);write(c/"external_data_sources.json",[{"node_id":sa},{"node_id":sb}])
 write(c/"process_data_sources.json",[{"process_name":"A","process_id":"process::A","configured_data_source_id":"configured-source::a","shared_source_id":sa,"source_type":"TM1_DIMENSION_SUBSET","dimension_name":"D","effective_hierarchy_name":"H1","subset_name":"Temp"},{"process_name":"B","process_id":"process::B","configured_data_source_id":"configured-source::b","shared_source_id":sb,"source_type":"TM1_DIMENSION_SUBSET","dimension_name":"D","effective_hierarchy_name":"H2","subset_name":"Temp"}])
 assert build(tmp_path)["status"]=="COMPLETE"
 refs=[x for x in module.read_json(c/"object_catalog.json") if x["object_type"]=="TM1_SUBSET_REFERENCE"]
 assert {tuple(x["identity_components"]) for x in refs}=={("TM1_SUBSET_REFERENCE","d","h1","temp"),("TM1_SUBSET_REFERENCE","d","h2","temp")}

def test_partial_preserves_current(tmp_path):
 c=base(tmp_path);write(c/"object_catalog.json",[{"sentinel":True}]);write(c/"graph_nodes.json",[{"node_id":"process::P","node_type":"PROCESS","object_name":"P"}]);write(c/"graph_relationships.json",[{"graph_relationship_id":"bad","source_id":"process::P","target_id":"missing","relationship_type":"CALLS_PROCESS"}]);write(c/"graph_provenance.json",[{"graph_relationship_id":"bad"}])
 assert build(tmp_path)["status"]=="PARTIAL";assert module.read_json(c/"object_catalog.json")==[{"sentinel":True}]
