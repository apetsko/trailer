import os
import subprocess
import re

def parse_time(t_str):
    # Заменяем точки на двоеточия: 00.05.36 -> 00:05:36
    return t_str.replace('.', ':').strip()

def main():
    # Названия файлов
    main_video = "Наполеон_2023_WEB-DLRip-AVC.mkv"
    timings_file = "timings.txt"
    output_video = "output.mkv"
    
    if not os.path.exists(main_video):
        print(f"Ошибка: Главный фильм '{main_video}' не найден в текущей папке.")
        return
        
    if not os.path.exists(timings_file):
        print(f"Файл '{timings_file}' не найден. Создаю шаблон...")
        with open(timings_file, "w", encoding='utf-8') as f:
            f.write("00.00.00-00.05.36\n")
            f.write("ad.mp4\n")
            f.write("00.05.55-end\n")
        print(f"Шаблон '{timings_file}' создан! Отредактируйте его и запустите скрипт снова.")
        return

    with open(timings_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    concat_list = []
    part_index = 0
    
    print("Начинаю нарезку фильма...")
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Проверяем, является ли строка интервалом (например, 00.05.36-00.05.55 или 00:05:36-end)
        match = re.match(r'^([\d\.:]+)\s*-\s*([\d\.:]+|end)$', line, re.IGNORECASE)
        
        if match:
            start_time = parse_time(match.group(1))
            end_time = match.group(2).lower()
            
            part_name = f"temp_part_{part_index}.mkv"
            print(f"⏳ Вырезаю фрагмент: {start_time} -> {end_time}...")
            
            # Команда для ffmpeg (без перекодировки, -c copy)
            cmd = ['ffmpeg', '-y', '-i', main_video, '-ss', start_time]
            if end_time != 'end':
                cmd.extend(['-to', parse_time(end_time)])
            cmd.extend(['-c', 'copy', part_name])
            
            # Запускаем ffmpeg, подавляя весь вывод кроме ошибок
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            concat_list.append(part_name)
            part_index += 1
        else:
            # Если это не интервал, значит это путь к рекламному ролику
            ad_file = line
            if os.path.exists(ad_file):
                print(f"✅ Добавляю рекламный ролик в очередь: {ad_file}")
                concat_list.append(ad_file)
            else:
                print(f"⚠️ Внимание: Рекламный ролик '{ad_file}' не найден! Он будет пропущен.")

    if not concat_list:
        print("Не найдено фрагментов для склейки.")
        return

    # Создаем текстовый файл для ffmpeg concat demuxer
    concat_file = "concat_list.txt"
    with open(concat_file, 'w', encoding='utf-8') as f:
        for item in concat_list:
            f.write(f"file '{item}'\n")
            
    print("🔄 Склеиваю все части воедино (это займет немного времени)...")
    concat_cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', 
        '-i', concat_file, '-c', 'copy', output_video
    ]
    subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"🎉 Готово! Итоговый фильм сохранен как: {output_video}")
    
    # Зачистка временных файлов
    print("🧹 Удаляю временные файлы...")
    for item in concat_list:
        if item.startswith("temp_part_"):
            try:
                os.remove(item)
            except:
                pass
    try:
        os.remove(concat_file)
    except:
        pass

if __name__ == "__main__":
    main()
