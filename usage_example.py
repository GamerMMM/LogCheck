#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FilterEngine 时间测量功能使用示例
展示如何在应用程序中使用搜索时间测量功能
"""

from logic.filter_engine import FilterEngine, SearchOptions

def example_basic_usage():
    """基本使用示例"""
    print("🔍 基本使用示例")
    print("-" * 40)
    
    # 创建过滤器引擎
    engine = FilterEngine(max_workers=4)
    
    # 模拟文本内容（实际应用中来自编辑器）
    sample_text = """
2024-01-01 10:00:00 INFO 应用程序启动
2024-01-01 10:00:01 DEBUG 初始化配置
2024-01-01 10:00:02 WARNING 配置文件未找到，使用默认配置
2024-01-01 10:00:03 ERROR 数据库连接失败
2024-01-01 10:00:04 INFO 尝试重新连接数据库
2024-01-01 10:00:05 ERROR 连接超时
2024-01-01 10:00:06 DEBUG 检查网络连接
2024-01-01 10:00:07 INFO 网络连接正常
2024-01-01 10:00:08 WARNING 数据库服务器响应缓慢
2024-01-01 10:00:09 SUCCESS 数据库连接成功
""".strip()
    
    # 设置搜索选项
    options = SearchOptions(
        show_only=False,      # 显示所有行
        ignore_alpha=True,    # 忽略大小写
        whole_pair=False      # 不要求整词匹配
    )
    
    # 执行搜索
    include_keywords = ["ERROR", "WARNING"]
    exclude_keywords = ["DEBUG"]
    
    print(f"搜索关键词: {include_keywords}")
    print(f"排除关键词: {exclude_keywords}")
    print(f"文本行数: {len(sample_text.splitlines())}")
    
    # 执行搜索
    result = engine.parallel_search_text(sample_text, include_keywords, exclude_keywords, options)
    
    # 显示结果
    print(f"\n✅ 搜索完成!")
    print(f"搜索耗时: {result.search_time:.3f}秒")
    print(f"匹配行数: {len(result.matched_lines)}")
    print(f"总行数: {result.statistics.get('total_lines', 0)}")
    
    # 显示匹配的行
    print(f"\n📋 匹配的行:")
    for line_num, line_content in result.matched_lines:
        print(f"  第{line_num+1}行: {line_content.strip()}")
    
    # 获取最后一次搜索时间
    last_time = engine.get_last_search_time()
    print(f"\n⏱️ 最后一次搜索总耗时: {last_time:.3f}秒")

def main():
    """主函数"""
    print("🔬 FilterEngine 时间测量功能使用示例")
    print("=" * 60)
    
    try:
        # 运行示例
        example_basic_usage()
        
        print("\n" + "=" * 60)
        print("✅ 示例运行完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ 运行示例时出现错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 