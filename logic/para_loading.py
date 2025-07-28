import threading

class ParaLoadFile:
    def _split_file(file_path, num_chunks):
        with open(file_path, 'r') as f:
            lines = f.readlines()
        chunk_size = len(lines) // num_chunks
        chunks = [lines[i:i + chunk_size] for i in range(0, len(lines), chunk_size)]
        return chunks

    def _read_chunk(chunk):
        try:
            data = ''.join(chunk)
            print(f'Reading chunk with {len(chunk)} lines')
            return data
        except Exception as e:
            print(f'Error reading chunk: {e}')
            return ''

    def _start_threads(chunks):
        threads = []
        results = []
        for chunk in chunks:
            thread = threading.Thread(target=lambda: results.append(ParaLoadFile._read_chunk(chunk)))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def _combine_results(results):   # 返回最终结果
        return ''.join(results)

    def main(file_path, num_chunks):
        chunks = ParaLoadFile._split_file(file_path, num_chunks)
        results = ParaLoadFile._start_threads(chunks)
        final_data = ParaLoadFile._combine_results(results)
        return final_data

# if __name__ == '__main__':
#     file_path = "D:/LSBT/LogCheck/algorithm-2024-03-04 -lager.txt"  # 替换为您的大文件路径
#     num_chunks = 4  # 设置并发线程数
#     final_data =  ParaLoadFile.main(file_path, num_chunks)
#     print(len(final_data))
