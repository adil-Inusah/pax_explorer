from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from scripts import collect_tm1_process_data_sources as module

class FakeProcessService:
    def __init__(self, processes): self._processes=processes
    def get_all_names(self): return [item.name for item in self._processes]
    def get(self,name): return next(item for item in self._processes if item.name==name)
class FakeTM1:
    def __init__(self,processes): self.processes=FakeProcessService(processes)
def read_json(path): return json.loads(path.read_text(encoding="utf-8"))
def collect(tmp_path, processes):
    return module.collect_process_data_sources(FakeTM1(processes),snapshot_root=tmp_path/"snapshots",current_root=tmp_path/"current",timestamp=datetime(2026,9,25,16,0,tzinfo=timezone.utc))
def ascii_process(name,path): return SimpleNamespace(name=name,datasource_type="ASCII",datasource_data_source_name_for_server=path,datasource_data_source_name_for_client=path,datasource_password="",datasource_user_name="")
def cube_view_process(name,cube,view): return SimpleNamespace(name=name,datasource_type="TM1CubeView",datasource_data_source_name_for_server=cube,datasource_data_source_name_for_client=cube,datasource_view=view,datasource_password="",datasource_user_name="")
def subset_process(name,dimension,hierarchy,subset): return SimpleNamespace(name=name,datasource_type="TM1DimensionSubset",dimension=dimension,hierarchy=hierarchy,datasource_subset=subset,datasource_password="",datasource_user_name="")

def test_ascii_contract_and_bridge(tmp_path):
    manifest=collect(tmp_path,[ascii_process("Load File","model_upload/input.csv")])
    assert manifest["status"]=="COMPLETE"
    rels=read_json(tmp_path/"current/process_data_source_relationships.json")
    read=next(x for x in rels if x["relationship_type"]=="READS_FROM_FILE")
    bridge=next(x for x in rels if x["relationship_type"]=="RESOLVES_TO_FILE")
    assert bridge["source_id"]==read["configured_data_source_id"]
    assert bridge["target_id"]==read["target_id"]

def test_shared_ascii_source_deduplicates_bridge(tmp_path):
    collect(tmp_path,[ascii_process("A","input.csv"),ascii_process("B","input.csv")])
    rels=read_json(tmp_path/"current/process_data_source_relationships.json")
    assert sum(x["relationship_type"]=="READS_FROM_FILE" for x in rels)==2
    assert sum(x["relationship_type"]=="RESOLVES_TO_FILE" for x in rels)==1

def test_same_view_name_different_cubes_is_distinct(tmp_path):
    collect(tmp_path,[cube_view_process("A","Cube A","Temp"),cube_view_process("B","Cube B","Temp")])
    sources=read_json(tmp_path/"current/external_data_sources.json")
    views=[x for x in sources if x["source_type"]=="TM1_CUBE_VIEW"]
    assert len(views)==2
    assert {x["effective_cube_name"] for x in views}=={"Cube A","Cube B"}
    assert len({x["node_id"] for x in views})==2

def test_same_cube_view_shared_but_declarations_unique(tmp_path):
    collect(tmp_path,[cube_view_process("A","Cube A","Temp"),cube_view_process("B","Cube A","Temp")])
    sources=read_json(tmp_path/"current/external_data_sources.json")
    decls=read_json(tmp_path/"current/process_data_sources.json")
    assert len([x for x in sources if x["source_type"]=="TM1_CUBE_VIEW"])==1
    assert len({x["configured_data_source_id"] for x in decls})==2
    assert len({x["shared_source_id"] for x in decls})==1
    assert {x["effective_cube_name"] for x in decls}=={"Cube A"}

def test_same_subset_name_different_dimensions_is_distinct(tmp_path):
    collect(tmp_path,[subset_process("A","D1","D1","Temp"),subset_process("B","D2","D2","Temp")])
    sources=read_json(tmp_path/"current/external_data_sources.json")
    subset=[x for x in sources if x["source_type"]=="TM1_DIMENSION_SUBSET"]
    assert len(subset)==2 and len({x["node_id"] for x in subset})==2

def test_same_subset_name_different_hierarchies_is_distinct(tmp_path):
    collect(tmp_path,[subset_process("A","D","H1","Temp"),subset_process("B","D","H2","Temp")])
    sources=read_json(tmp_path/"current/external_data_sources.json")
    subset=[x for x in sources if x["source_type"]=="TM1_DIMENSION_SUBSET"]
    assert {x["effective_hierarchy_name"] for x in subset}=={"H1","H2"}

def test_relationship_validation_identities_reconcile(tmp_path):
    manifest=collect(tmp_path,[ascii_process("A","input.csv")])
    rels=read_json(tmp_path/"current/process_data_source_relationships.json")
    vals=read_json(tmp_path/"current/process_data_source_validations.json")
    assert manifest["relationship_count"]==manifest["validation_count"]
    assert {x["relationship_id"] for x in rels}=={x["relationship_id"] for x in vals}
