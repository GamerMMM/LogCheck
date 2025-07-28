#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
搜索性能测试脚本
用于测试 FilterEngine 的搜索性能
"""

import time
from logic.filter_engine import FilterEngine, SearchOptions

def generate_test_data(lines: int = 10000) -> str:
    """生成测试数据"""
    print(f"📝 生成 {lines} 行测试数据...")
    
    test_lines = []
    for i in range(lines):
        if i % 100 == 0:
            test_lines.append(f"第 {i} 行 - 包含关键词 ERROR 和 WARNING")
        elif i % 50 == 0:
            test_lines.append(f"第 {i} 行 - 包含关键词 INFO 和 DEBUG")
        elif i % 25 == 0:
            test_lines.append(f"第 {i} 行 - 包含关键词 SUCCESS")
        else:
            test_lines.append(f"第 {i} 行 - 普通日志信息")
    
    return "\n".join(test_lines)

def test_basic_search():
    """测试基本搜索功能"""
    print("=" * 60)
    print("🔍 基本搜索测试")
    print("=" * 60)
    
    # 创建过滤器引擎
    engine = FilterEngine(max_workers=4)
    
    # 生成测试数据
    test_data = generate_test_data(5000)
    
    # 设置搜索选项
    options = SearchOptions(
        show_only=False,
        ignore_alpha=False,
        whole_pair=False
    )
    
    # 执行搜索
    include_keywords = ["ERROR", "WARNING"]
    exclude_keywords = ["DEBUG"]
    
    print(f"搜索关键词: {include_keywords}")
    print(f"排除关键词: {exclude_keywords}")
    print(f"测试数据行数: {len(test_data.splitlines())}")
    
    # 执行搜索并测量时间
    result = engine.parallel_search_text(test_data, include_keywords, exclude_keywords, options)
    
    print(f"✅ 搜索完成!")
    print(f"   - 搜索耗时: {result.search_time:.3f}秒")
    print(f"   - 匹配行数: {len(result.matched_lines)}")
    print(f"   - 总行数: {result.statistics.get('total_lines', 0)}")

def test_performance_benchmark():
    """性能基准测试"""
    print("\n" + "=" * 60)
    print("🚀 性能基准测试")
    print("=" * 60)
    
    # 创建过滤器引擎
    engine = FilterEngine(max_workers=4)
    
    # 生成不同大小的测试数据
    test_sizes = [1000, 5000, 10000, 20000]
    
    for size in test_sizes:
        print(f"\n📊 测试 {size} 行数据:")
        test_data = generate_test_data(size)
        
        options = SearchOptions(
            show_only=False,
            ignore_alpha=False,
            whole_pair=False
        )
        
        include_keywords = ["ERROR", "WARNING", "INFO"]
        exclude_keywords = ["DEBUG"]
        
        # 运行性能测试
        performance_stats = engine.measure_search_performance(
            test_data, 
            include_keywords, 
            exclude_keywords, 
            options, 
            iterations=3
        )
        
        print(f"   📈 性能总结:")
        print(f"      - 平均耗时: {performance_stats['average_time']:.3f}秒")
        print(f"      - 处理速度: {size / performance_stats['average_time']:.0f} 行/秒")

def test_different_options():
    """测试不同搜索选项的性能"""
    print("\n" + "=" * 60)
    print("⚙️ 不同搜索选项性能测试")
    print("=" * 60)
    
    # 创建过滤器引擎
    engine = FilterEngine(max_workers=4)
    
    # 生成测试数据
    test_data = generate_test_data(10000)
    
    # 测试不同的搜索选项组合
    test_cases = [
        {
            "name": "基本搜索",
            "options": SearchOptions(show_only=False, ignore_alpha=False, whole_pair=False)
        },
        {
            "name": "忽略大小写",
            "options": SearchOptions(show_only=False, ignore_alpha=True, whole_pair=False)
        },
        {
            "name": "整词匹配",
            "options": SearchOptions(show_only=False, ignore_alpha=False, whole_pair=True)
        },
        {
            "name": "只显示匹配",
            "options": SearchOptions(show_only=True, ignore_alpha=False, whole_pair=False)
        },
        {
            "name": "忽略大小写 + 整词匹配",
            "options": SearchOptions(show_only=False, ignore_alpha=True, whole_pair=True)
        }
    ]
    
    include_keywords = ["ERROR", "WARNING"]
    exclude_keywords = ["DEBUG"]
    
    for test_case in test_cases:
        print(f"\n🔍 {test_case['name']}:")
        
        # 清除缓存以确保公平测试
        engine.clear_cache()
        
        start_time = time.time()
        result = engine.parallel_search_text(test_data, include_keywords, exclude_keywords, test_case['options'])
        end_time = time.time()
        
        search_time = end_time - start_time
        
        print(f"   - 搜索耗时: {search_time:.3f}秒")
        print(f"   - 匹配行数: {len(result.matched_lines)}")
        print(f"   - 处理速度: {len(test_data.splitlines()) / search_time:.0f} 行/秒")

def test_cache_performance():
    """测试缓存性能"""
    print("\n" + "=" * 60)
    print("💾 缓存性能测试")
    print("=" * 60)
    
    # 创建过滤器引擎
    engine = FilterEngine(max_workers=4)
    
    # 生成测试数据
    test_data = generate_test_data(5000)
    
    options = SearchOptions(show_only=False, ignore_alpha=False, whole_pair=False)
    include_keywords = ["ERROR", "WARNING"]
    exclude_keywords = ["DEBUG"]
    
    print("🔄 第一次搜索（无缓存）:")
    start_time = time.time()
    result1 = engine.parallel_search_text(test_data, include_keywords, exclude_keywords, options)
    first_search_time = time.time() - start_time
    
    print(f"   - 搜索耗时: {first_search_time:.3f}秒")
    print(f"   - 匹配行数: {len(result1.matched_lines)}")
    
    print("\n🔄 第二次搜索（有缓存）:")
    start_time = time.time()
    result2 = engine.parallel_search_text(test_data, include_keywords, exclude_keywords, options)
    second_search_time = time.time() - start_time
    
    print(f"   - 搜索耗时: {second_search_time:.3f}秒")
    print(f"   - 匹配行数: {len(result2.matched_lines)}")
    
    if second_search_time > 0:
        speedup = first_search_time / second_search_time
        print(f"   - 缓存加速比: {speedup:.1f}x")
    else:
        print(f"   - 缓存加速比: 极快（接近0秒）")
    
    # 显示缓存统计
    cache_stats = engine.get_cache_stats()
    print(f"   - 缓存大小: {cache_stats['cache_size']} 项")

def main():
    """主函数"""
    print("🔬 FilterEngine 搜索性能测试")
    print("=" * 60)
    
    try:
        # 运行各种测试
        test_basic_search()
        test_performance_benchmark()
        test_different_options()
        test_cache_performance()
        
        print("\n" + "=" * 60)
        print("✅ 所有测试完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 