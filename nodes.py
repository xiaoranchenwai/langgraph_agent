import json
import logging
from typing import Annotated, Literal
from langchain_core.messages import AIMessage, HumanMessage,  SystemMessage, ToolMessage
from langgraph.types import Command, interrupt
from langchain_openai import ChatOpenAI
from state import State
from prompts import *
from tools import *


llm = ChatOpenAI(model="qwen3-235b", temperature=0.0, base_url='http://10.250.2.25:8004/v1', api_key='**')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
hander = logging.StreamHandler()
hander.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
hander.setFormatter(formatter)
logger.addHandler(hander)

def extract_json(text):
    if '```json' not in text:
        return text
    text = text.split('```json')[1].split('```')[0].strip()
    return text

def extract_answer(text):
    if '</think>' in text:
        answer = text.split("</think>")[-1]
        return answer.strip()
    
    return text


def parse_tools(text, start_flag, end_flag):
  
    tools = text.split(start_flag)
    tools = [tool for tool in tools if end_flag in tool]
    if tools:
        tools = [tool.split(end_flag)[0].strip() for tool in tools]
    return tools




def get_tools(response):
    print(extract_answer(response['content']))
    if response['tool_calls']:
        print("-------------------------")
        tools = response['tool_calls']

    else:
        content = extract_answer(response['content'])
        

        if '<tool_call>' in content:
            print("----------<tool_call>------------")
            tools = parse_tools(content, '<tool_call>', '</tool_call>')
            
        elif '<function_call>' in content:
            print("----------<function_call>------------")
            tools = parse_tools(content, '<function_call>', '</function_call>')
            
        elif '```json' in content:
            print("----------<```json>------------")
            tools = parse_tools(content, '```json', '```')
            
        else:
            tools = []
            
    return tools
            
# 新增：任务分类节点
def task_classifier_node(state: State):
    """任务分类节点，判断是Excel分析任务还是通用任务"""
    logger.info("***正在运行task_classifier_node***")
    
    user_message = state['user_message']
    messages = [HumanMessage(content=TASK_CLASSIFIER_PROMPT.format(user_message=user_message))]
    
    response = llm.invoke(messages)
    response = response.model_dump_json(indent=4, exclude_none=True)
    response = json.loads(response)
    
    try:
        classification_result = json.loads(extract_json(extract_answer(response['content'])))
        task_type = classification_result.get('task_type', 'general_task')
        reasoning = classification_result.get('reasoning', '默认分类')
        
        logger.info(f"任务分类结果: {task_type}, 理由: {reasoning}")
        
        # 根据任务类型跳转到不同的节点
        if task_type == 'excel_analysis':
            return Command(goto="data_header_analysis_node", update={"task_type": task_type})
        else:
            return Command(goto="general_create_planner", update={"task_type": task_type})
            
    except Exception as e:
        logger.error(f"任务分类失败: {e}")
        # 默认跳转到通用任务
        return Command(goto="general_create_planner", update={"task_type": "general_task"})

# 新增：通用任务创建计划节点
def general_create_planner_node(state: State):
    """通用任务创建计划节点"""
    logger.info("***正在运行General Create Planner node***")
    
    user_message = state['user_message']
    messages = [
        SystemMessage(content=GENERAL_PLAN_SYSTEM_PROMPT), 
        HumanMessage(content=GENERAL_PLAN_CREATE_PROMPT.format(input_message=user_message))
    ]
    
    response = llm.invoke(messages)
    response = response.model_dump_json(indent=4, exclude_none=True)
    response = json.loads(response)
    
    try:
        plan = json.loads(extract_json(extract_answer(response['content'])))
        logger.info(f"通用任务计划创建成功: {plan.get('goal', 'N/A')}")
        
        state['messages'] += [AIMessage(content=json.dumps(plan, ensure_ascii=False))]
        return Command(goto="general_execute", update={"plan": plan})
        
    except Exception as e:
        logger.error(f"通用任务计划创建失败: {e}")
        # 重试机制
        messages += [HumanMessage(content=f"JSON格式错误: {e}, 请重新生成计划")]
        return general_create_planner_node(state)

# 新增：通用任务更新计划节点
def general_update_planner_node(state: State):
    """通用任务更新计划节点"""
    logger.info("***正在运行General Update Planner node***")
    
    plan = state['plan']
    goal = plan['goal']
    
    messages = [
        SystemMessage(content=GENERAL_PLAN_SYSTEM_PROMPT),
        HumanMessage(content=GENERAL_UPDATE_PLAN_PROMPT.format(plan=plan, goal=goal))
    ]
    
    while True:
        try:
            response = llm.invoke(messages)
            response = response.model_dump_json(indent=4, exclude_none=True)
            response = json.loads(response)
            
            updated_plan = json.loads(extract_json(extract_answer(response['content'])))
            logger.info(f"通用任务计划更新成功")
            
            state['messages'] += [AIMessage(content=json.dumps(updated_plan, ensure_ascii=False))]
            return Command(goto="general_execute", update={"plan": updated_plan})
            
        except Exception as e:
            logger.error(f"通用任务计划更新失败: {e}")
            messages += [HumanMessage(content=f"JSON格式错误: {e}, 请重新生成更新的计划")]

# 新增：通用任务执行节点
def general_execute_node(state: State):
    """通用任务执行节点"""
    logger.info("***正在运行General Execute node***")
    
    plan = state['plan']
    steps = plan['steps']
    current_step = None
    current_step_index = 0
    
    # 获取第一个未完成的步骤
    for i, step in enumerate(steps):
        if step['status'] == 'pending':
            current_step = step
            current_step_index = i
            break
    
    logger.info(f"当前执行步骤: {current_step}")
    
    # 如果没有待执行步骤，跳转到报告节点
    if current_step is None:
        return Command(goto='report')
    
    # 构建执行消息
    messages = state['observations'] + [
        SystemMessage(content=GENERAL_EXECUTE_SYSTEM_PROMPT),
        HumanMessage(content=GENERAL_EXECUTION_PROMPT.format(
            user_message=state["user_message"], 
            step=current_step['description']
        ))
    ]
    
    tool_result = None
    while True:
        response = llm.bind_tools([create_file, str_replace, shell_exec]).invoke(messages)
        response = response.model_dump_json(indent=4, exclude_none=True)
        response = json.loads(response)
        
        tools = {"create_file": create_file, "str_replace": str_replace, "shell_exec": shell_exec}
        extract_tools = get_tools(response)
        
        if extract_tools:
            for tool in extract_tools:
                if isinstance(tool, str):
                    try:
                        tool = json.loads(tool)
                    except Exception as e:
                        messages += [HumanMessage(content=f"{tool}json格式错误:{e}")]
                        break
                        
                    if isinstance(tool, list):
                        tool = tool[0]
                
                try:
                    tool_name = tool['name']
                    keys = list(tool.keys())
                    tool_args = tool[keys[1]]
                except Exception as e:
                    messages += [HumanMessage(content=f"{tool}工具调用格式错误:{e}")]
                    break
                
                tool_result = tools[tool_name].invoke(tool_args)
                logger.info(f"tool_name:{tool_name}, tool_args:{tool_args}\ntool_result:{tool_result}")
                
                if 'id' in tool:
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [ToolMessage(content=f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}", tool_call_id=tool['id'])]
                else:
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [HumanMessage(content=f"tool_result:{tool_result}")]
        else:
            # 步骤执行完成，更新状态
            logger.info(f"通用任务步骤执行完成:{extract_answer(response['content'])}")
            
            # 标记当前步骤为已完成
            steps[current_step_index]['status'] = 'completed'
            updated_plan = plan.copy()
            updated_plan['steps'] = steps
            
            state['messages'] += [AIMessage(content=extract_answer(response['content']))]
            if tool_result:
                state['observations'] += [AIMessage(content=f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}")]
            state['observations'] += [AIMessage(content=extract_answer(response['content']))]
            
            # 检查是否还有未完成的步骤
            remaining_steps = [step for step in steps if step['status'] == 'pending']
            if remaining_steps:
                return Command(goto='general_update_planner', update={'plan': updated_plan})
            else:
                return Command(goto='report', update={'plan': updated_plan})            
    


def data_header_analysis_node(state: State):
    logger.info("***正在运行data_header_analysis_node***")
    messages = [SystemMessage(content=DATA_HEADER_ANALYSIS_PROMPT.format(user_message=state['user_message']))]
    while True:
        response = llm.bind_tools([create_file, str_replace, shell_exec]).invoke(messages)
        response = response.model_dump_json(indent=4, exclude_none=True)
        response = json.loads(response)
        tools = {"create_file": create_file, "str_replace": str_replace, "shell_exec": shell_exec}  
        extract_tools = get_tools(response)   
        if extract_tools:
            for tool in extract_tools:
                if isinstance(tool, str):
                    try:
                        tool = json.loads(tool)  
                    except Exception as e:
                        messages += [HumanMessage(content=f"{tool}json格式错误:{e}")]
                        break
                        
                    if isinstance(tool, list):
                        tool = tool[0]
                print(tool)
                try:
                    tool_name = tool['name']
                    keys = list(tool.keys())
                    tool_args = tool[keys[1]]
                except Exception as e:
                    messages += [HumanMessage(content=f"{tool}工具调用格式错误:{e}")]
                    break
                tool_result = tools[tool_name].invoke(tool_args)
                logger.info(f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}")
                if 'id' in tool:
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [ToolMessage(content=f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}", tool_call_id=tool['id'])]
                else:
                    # messages += [HumanMessage(content=f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}")]
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [HumanMessage(content=f"tool_result:{tool_result}")]
        else:
            state['messages'] += [AIMessage(content=extract_answer(response['content']))]
            return Command(goto="create_planner")
        

        

def create_planner_node(state: State):
    logger.info("***正在运行Create Planner node***")
    input_message = state['messages'][-1].content
    print(input_message)
    messages = [SystemMessage(content=PLAN_SYSTEM_PROMPT), HumanMessage(content=PLAN_CREATE_PROMPT.format(input_message = input_message))]
    response = llm.invoke(messages)
    response = response.model_dump_json(indent=4, exclude_none=True)
    response = json.loads(response)
    plan = json.loads(extract_json(extract_answer(response['content'])))
    print(plan)
    state['messages'] += [AIMessage(content=json.dumps(plan, ensure_ascii=False))]
    return Command(goto="execute", update={"plan": plan})

def update_planner_node(state: State):
    logger.info("***正在运行Update Planner node***")
    plan = state['plan']
    goal = plan['goal']
    state['messages'].extend([SystemMessage(content=PLAN_SYSTEM_PROMPT), HumanMessage(content=UPDATE_PLAN_PROMPT.format(plan = plan, goal=goal))])
    messages = state['messages']
    while True:
        try:
            response = llm.invoke(messages)
            response = response.model_dump_json(indent=4, exclude_none=True)
            response = json.loads(response)
            plan = json.loads(extract_json(extract_answer(response['content'])))
            state['messages']+=[AIMessage(content=json.dumps(plan, ensure_ascii=False))]
            return Command(goto="execute", update={"plan": plan})
        except Exception as e:
            messages += [HumanMessage(content=f"{extract_json(extract_answer(response['content']))}json格式错误:{e}")]
            
def execute_node(state: State):
    logger.info("***正在运行execute_node***")
  
    plan = state['plan']
    steps = plan['steps']
    current_step = None
    current_step_index = 0
    
    # 获取第一个未完成STEP
    for i, step in enumerate(steps):
        status = step['status']
        if status == 'pending':
            current_step = step
            current_step_index = i
            break
        
    logger.info(f"当前执行STEP:{current_step}")
    
    ## 此处只是简单跳转到report节点，实际应该根据当前STEP的描述进行判断
    if current_step is None or current_step_index == len(steps)-1:
        return Command(goto='report')
    
    messages = state['observations'] + [SystemMessage(content=EXECUTE_SYSTEM_PROMPT), HumanMessage(content=EXECUTION_PROMPT.format(user_message = state["user_message"], step=current_step['description']))]
    # messages = [SystemMessage(content=EXECUTE_SYSTEM_PROMPT), HumanMessage(content=EXECUTION_PROMPT.format(user_message = state["user_message"], step=current_step['description']))]
    
    tool_result = None
    while True:
        response = llm.bind_tools([create_file, str_replace, shell_exec]).invoke(messages)
        response = response.model_dump_json(indent=4, exclude_none=True)
        response = json.loads(response)
        tools = {"create_file": create_file, "str_replace": str_replace, "shell_exec": shell_exec}   
        extract_tools = get_tools(response)   
        if extract_tools:
            for tool in extract_tools:
                if isinstance(tool, str):
                    try:
                        tool = json.loads(tool)  
                    except Exception as e:
                        messages += [HumanMessage(content=f"{tool}json格式错误:{e}")]
                        break
                        
                    if isinstance(tool, list):
                        tool = tool[0]
                
                try:
                    tool_name = tool['name']
                    keys = list(tool.keys())
                    tool_args = tool[keys[1]]
                except Exception as e:
                    messages += [HumanMessage(content=f"{tool}工具调用格式错误:{e}")]
                    break
                
                tool_result = tools[tool_name].invoke(tool_args)
                logger.info(f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}")
                if 'id' in tool:
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [ToolMessage(content=f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}", tool_call_id=tool['id'])]
                else:
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [HumanMessage(content=f"tool_result:{tool_result}")] 
        
        else:
            logger.info(f"当前STEP执行总结:{extract_answer(response['content'])}")
            state['messages'] += [AIMessage(content=extract_answer(response['content']))]
            if tool_result:
                state['observations'] += [AIMessage(content=f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}")]
            state['observations'] += [AIMessage(content=extract_answer(response['content']))]
            
            return Command(goto='update_planner', update={'plan': plan})
    

    
def report_node(state: State):
    """Report node that write a final report."""
    logger.info("***正在运行report_node***")
    
    # observations = state.get("observations")
    # messages = observations + [SystemMessage(content=REPORT_PROMPT)]
    
    observations = state.get("observations")
    observations = "\n\n".join([observation.content for observation in observations])
    print(observations)
    messages = [HumanMessage(content=REPORT_PROMPT.format(observations = observations))]
    
    while True:
        response = llm.bind_tools([create_file, shell_exec]).invoke(messages)
        response = response.model_dump_json(indent=4, exclude_none=True)
        response = json.loads(response)
        tools = {"create_file": create_file, "shell_exec": shell_exec} 
        extract_tools = get_tools(response)   
        if extract_tools:
            for tool in extract_tools:
                print(tool)
                if isinstance(tool, str):
                    try:
                        tool = json.loads(tool)  
                    except Exception as e:
                        messages += [HumanMessage(content=f"{tool}json格式错误:{e}")]
                        break
                        
                    if isinstance(tool, list):
                        tool = tool[0]
                
                try:
                    tool_name = tool['name']
                    keys = list(tool.keys())

                    tool_args = tool[keys[1]]
                    
                except Exception as e:
                    messages += [HumanMessage(content=f"{tool}工具调用格式错误:{e}")]
                    break
                
                tool_result = tools[tool_name].invoke(tool_args)
                logger.info(f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}")
                if 'id' in tool:
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [ToolMessage(content=f"tool_name:{tool_name},tool_args:{tool_args}\ntool_result:{tool_result}", tool_call_id=tool['id'])]
                else:
                    messages += [AIMessage(content=extract_answer(response['content']))]
                    messages += [HumanMessage(content=f"tool_result:{tool_result}")] 
        
        else:
            break
    
    print(extract_answer(response['content']))      
    return {"final_report": extract_answer(response['content'])}



