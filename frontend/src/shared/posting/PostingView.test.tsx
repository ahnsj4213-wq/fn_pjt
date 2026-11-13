import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PostingPublic } from '@/api/common/postings';
import { PostingView } from './PostingView';

const base: PostingPublic = {
  id: 'p1', title: '백엔드 엔지니어 (주문·물류 시스템)', company_name: '컬리', company_intro: null, job_family: 'DEV',
  job_family_label: '개발', requirements: 'Spring Boot', preferred: null, duties: 'Kafka 이벤트 처리', headcount: null,
  experience_level: '경력 3년 이상', employment_type: '테스트용 가상 공고(채용 아님)', location: null, image_url: null,
  question_areas: [], deadline_date: null, deadline_at: null, is_open: true, coding_enabled: false, coding_language: null,
  coding_time_limit_min: null, has_applied: false, is_virtual: true,
  sources: [
    { title: 'Kafka Consumer 오프셋 이동하기', url: 'https://helloworld.kurly.com/blog/kafka/', published_date: '2024-12-02' },
    { title: '날짜 없는 글', url: 'https://helloworld.kurly.com/blog/nodate/', published_date: null },
  ],
};

describe('공고 상세 (ADR-0029 테스트용 가상 공고)', () => {
  it('says it is not a real posting, shows no invented deadline and links its blog sources', () => {
    render(<PostingView posting={base} />);
    expect(screen.getByTestId('virtual-notice').textContent).toContain('실제로 게시한 채용공고가 아니며');
    expect(screen.getAllByText('테스트용 가상 공고').length).toBeGreaterThan(0);
    expect(screen.getByText('테스트용 · 마감일 없음')).toBeTruthy();
    expect(screen.queryByText('상시')).toBeNull();
    const link = screen.getByRole('link', { name: 'Kafka Consumer 오프셋 이동하기' });
    expect(link.getAttribute('href')).toBe('https://helloworld.kurly.com/blog/kafka/');
    expect(link.getAttribute('rel')).toContain('noopener');
    expect(screen.getByText(/게시일 정보 없음/)).toBeTruthy();
  });

  it('shows nothing of this for an ordinary posting', () => {
    render(<PostingView posting={{ ...base, is_virtual: false, sources: [], deadline_date: '2099-12-31', employment_type: '정규직' }} />);
    expect(screen.queryByTestId('virtual-notice')).toBeNull();
    expect(screen.queryByTestId('posting-sources')).toBeNull();
    expect(screen.queryByText('테스트용 가상 공고')).toBeNull();
  });
});
