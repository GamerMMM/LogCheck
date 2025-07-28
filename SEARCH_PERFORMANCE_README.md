# FilterEngine 搜索时间测量功能

## 概述

为 `FilterEngine` 类添加了完整的时间测量功能，可以精确测量搜索性能，帮助优化搜索算法和识别性能瓶颈。

## 新增功能

### 1. 自动时间测量

每次搜索都会自动记录以下时间信息：

- **搜索处理时间**: 纯搜索算法执行时间
- **编辑器应用时间**: 将结果应用到编辑器的时间
- **总耗时**: 从开始到结束的完整时间

### 2. 性能测试方法

新增 `measure_search_performance()` 方法，可以：
- 多次运行搜索取平均值
- 计算最短/最长/平均耗时
- 显示处理速度（行/秒）
- 提供详细的性能统计

### 3. 时间查询方法

- `get_last_search_time()`: 获取最后一次搜索的总耗时
- 搜索结果中包含 `search_time` 字段

## 使用方法

### 基本使用

```python
from logic.filter_engine import FilterEngine, SearchOptions

# 创建过滤器引擎
engine = FilterEngine(max_workers=4)

# 设置搜索选项
options = SearchOptions(
    show_only=False,
    ignore_alpha=True,
    whole_pair=False
)

# 执行搜索
result = engine.parallel_search_text(
    text_content, 
    include_keywords=["ERROR", "WARNING"],
    exclude_keywords=["DEBUG"],
    options=options
)

# 查看搜索时间
print(f"搜索耗时: {result.search_time:.3f}秒")
print(f"匹配行数: {len(result.matched_lines)}")
```

### 性能测试

```python
# 运行性能测试（多次搜索取平均值）
performance_stats = engine.measure_search_performance(
    text_content=large_text,
    include_keywords=["ERROR", "WARNING"],
    exclude_keywords=["DEBUG"],
    options=options,
    iterations=5  # 运行5次取平均值
)

print(f"平均耗时: {performance_stats['average_time']:.3f}秒")
print(f"处理速度: {performance_stats['total_lines'] / performance_stats['average_time']:.0f} 行/秒")
```

## 测试脚本

### 运行性能测试

```bash
python test_search_performance.py
```

### 运行使用示例

```bash
python usage_example.py
```

## 输出示例

### 搜索完成输出

```
🔍 搜索完成 - 总耗时: 0.156秒
   - 搜索处理时间: 0.123秒
   - 编辑器应用时间: 0.033秒
   - 匹配行数: 67
   - 总行数: 5000
```

### 性能测试输出

```
🚀 开始性能测试 - 运行 3 次搜索...
   第 1 次测试...
   第 1 次耗时: 0.145秒, 匹配行数: 67
   第 2 次测试...
   第 2 次耗时: 0.138秒, 匹配行数: 67
   第 3 次测试...
   第 3 次耗时: 0.142秒, 匹配行数: 67
📊 性能测试结果:
   - 平均耗时: 0.142秒
   - 最短耗时: 0.138秒
   - 最长耗时: 0.145秒
   - 总行数: 5000
   - 匹配行数: 67
``` 