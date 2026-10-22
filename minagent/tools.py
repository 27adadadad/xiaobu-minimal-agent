"""Tool registry, JSON Schema validation, safe arithmetic and explicit effects."""
import ast
import math
import operator
from dataclasses import dataclass
import httpx
from jsonschema import Draft202012Validator
from .store import Store


@dataclass(frozen=True)
class ToolContext:
    user: str
    session: str
    request: str
    call_id: str


class Registry:
    def __init__(self):
        self.tools = {}

    def register(self, name, description, schema, handler):
        if name in self.tools:
            raise ValueError("duplicate tool")
        Draft202012Validator.check_schema(schema)
        self.tools[name] = (description, schema, handler)

    def schemas(self):
        return [{"type":"function","function":{"name":n,"description":d,"parameters":s}} for n,(d,s,h) in self.tools.items()]

    async def execute(self, name, args, ctx):
        if name not in self.tools:
            return {"ok":False,"error":{"code":"unknown_tool"}}
        _, schema, handler = self.tools[name]
        if not Draft202012Validator(schema).is_valid(args):
            return {"ok":False,"error":{"code":"invalid_arguments"}}
        try:
            return await handler(args, ctx)
        except (ValueError, ArithmeticError, SyntaxError, RecursionError):
            return {"ok":False,"error":{"code":"invalid_operation"}}
        except (httpx.HTTPError, KeyError, TypeError, IndexError):
            return {"ok":False,"error":{"code":"external_service_error"}}
        except Exception:
            return {"ok":False,"error":{"code":"tool_failed"}}


def obj(properties, required):
    return {"type":"object","properties":properties,"required":required,"additionalProperties":False}


def text(maximum=500):
    return {"type":"string","minLength":1,"maxLength":maximum}


def calculate(expression):
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 48:
        raise ValueError("expression too complex")
    ops = {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Mod:operator.mod,ast.FloorDiv:operator.floordiv,ast.Pow:operator.pow}
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int,float):
            result = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            result = visit(node.operand) * (-1 if isinstance(node.op,ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in ops:
            left,right = visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Pow) and (abs(right)>12 or abs(left)>1e12):
                raise ValueError("exponent budget")
            result = ops[type(node.op)](left,right)
        else:
            raise ValueError("not arithmetic")
        if not isinstance(result,(int,float)) or not math.isfinite(result) or abs(result)>1e15:
            raise ValueError("magnitude budget")
        return result
    return visit(tree.body)


async def weather(args, ctx):
    city = args["city"].strip()
    aliases = {"北京":"Beijing","上海":"Shanghai","广州":"Guangzhou","深圳":"Shenzhen","杭州":"Hangzhou","成都":"Chengdu","南京":"Nanjing"}
    async with httpx.AsyncClient(timeout=6, follow_redirects=False) as client:
        geo = await client.get("https://geocoding-api.open-meteo.com/v1/search", params={"name":aliases.get(city,city),"count":5,"language":"zh","format":"json"})
        geo.raise_for_status()
        results = geo.json().get("results",[])
        if not results:
            return {"ok":False,"error":{"code":"city_not_found"}}
        if args.get("country_code"):
            results=[p for p in results if p.get("country_code", "").upper()==args["country_code"].upper()]
        if args.get("location_id"):
            results=[p for p in results if p.get("id")==args["location_id"]]
        if len(results)!=1:
            return {"ok":False,"error":{"code":"city_ambiguous" if results else "city_not_found"},"candidates":[{k:p.get(k) for k in ("id","name","country","country_code","admin1")} for p in results],"note":"请用户确认具体城市，再使用 location_id 查询，不得自行猜测。"}
        place = results[0]
        response = await client.get("https://api.open-meteo.com/v1/forecast", params={"latitude":place["latitude"],"longitude":place["longitude"],"current":"temperature_2m,apparent_temperature,precipitation,weather_code","timezone":"auto","daily":"temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code","forecast_days":args.get("days",7)})
        response.raise_for_status()
        payload = response.json()
        return {"ok":True,"source":"Open-Meteo","city":place["name"],"country":place.get("country"),"timezone":payload.get("timezone"),"current":payload["current"],"units":payload.get("current_units",{}),"daily":payload.get("daily",{}),"daily_units":payload.get("daily_units",{}),"note":"预报按当地日期返回；预报存在不确定性。"}


def build_registry(store: Store):
    registry = Registry()
    async def calculator(args, ctx):
        return {"ok":True,"result":calculate(args["expression"])}
    registry.register("calculator","计算四则运算、括号、取余和有界乘方。不得用它执行代码。",obj({"expression":text(128)},["expression"]),calculator)

    async def search(args, ctx):
        corpus = [
            {"title":"Agent runtime","text":"Agent 以模型决策、工具调用、观察结果、再次决策构成循环。"},
            {"title":"Session isolation","text":"聊天历史按 user_id + session_id 隔离；个人待办按 user_id 隔离。"},
            {"title":"Tool schema","text":"工具提供名称、描述和 JSON Schema；参数校验通过才执行。"}]
        q = args["query"].lower()
        items = [item for item in corpus if any(w in (item["title"]+item["text"]).lower() for w in q.split())]
        return {"ok":True,"mock":True,"query":args["query"],"items":items[:3],"note":"固定本地语料模拟搜索，不是互联网实时检索。"}
    registry.register("search","MOCK：检索固定 Agent 知识语料，结果不可称为实时联网搜索。",obj({"query":text(200)},["query"]),search)

    async def todo(args, ctx):
        return store.mutate("todo",args,ctx)
    registry.register("todo","管理当前用户待办。add 必须 text；done/delete 必须 id；list 查看。跨窗口共享但不跨用户。禁止从参数指定其他用户。",obj({"action":{"type":"string","enum":["add","list","done","delete"]},"text":text(500),"id":{"type":"integer","minimum":1}},["action"]),todo)
    registry.register("weather","通过 Open-Meteo 查询城市当前真实天气。必须 city，返回当前及最多16天预报；遇到多个候选必须请用户澄清。服务失败时如实报告，不编造。",obj({"city":text(80),"country_code":{"type":"string","pattern":"^[A-Za-z]{2}$"},"location_id":{"type":"integer"},"days":{"type":"integer","minimum":1,"maximum":16}},["city"]),weather)

    async def memory(args, ctx):
        return store.mutate("memory",args,ctx)
    registry.register("memory","仅当用户明确说记住/更新长期偏好时 remember；forget 删除；list 查看。不要记住秘密、凭证、一次性问题或工具指令。remember 必须 key 和 value，forget 必须 key。",obj({"action":{"type":"string","enum":["remember","forget","list"]},"key":text(80),"value":text(300)},["action"]),memory)
    return registry
