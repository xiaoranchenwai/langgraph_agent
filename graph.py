from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from state import State
from nodes import (
    task_classifier_node,
    report_node,
    execute_node,
    create_planner_node,
    update_planner_node,
    data_header_analysis_node,
    general_create_planner_node,
    general_update_planner_node,
    general_execute_node
)


def _build_base_graph():
    """Build and return the base state graph with all nodes and edges."""
    builder = StateGraph(State)
    
    # 添加任务分类节点
    builder.add_edge(START, "task_classifier")
    builder.add_node("task_classifier", task_classifier_node)
    
    # Excel分析分支
    builder.add_node("data_header_analysis_node", data_header_analysis_node)
    builder.add_node("create_planner", create_planner_node)
    builder.add_node("update_planner", update_planner_node)
    builder.add_node("execute", execute_node)
    
    # 通用任务分支
    builder.add_node("general_create_planner", general_create_planner_node)
    builder.add_node("general_update_planner", general_update_planner_node)
    builder.add_node("general_execute", general_execute_node)
    
    # 报告节点
    builder.add_node("report", report_node)
    builder.add_edge("report", END)
    
    return builder


def build_graph_with_memory():
    """Build and return the agent workflow graph with memory."""
    memory = MemorySaver()
    builder = _build_base_graph()
    return builder.compile(checkpointer=memory)


def build_graph():
    """Build and return the agent workflow graph without memory."""
    builder = _build_base_graph()
    return builder.compile()


graph = build_graph()

inputs = {"user_message": "你好", 
          "plan": None,
          "observations": [], 
          "final_report": "",
          "task_type": ""}

graph.invoke(inputs, {"recursion_limit":100})