// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "popl20-min-max-dec.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

typedef unsigned int size_t;
extern void *malloc (size_t __size) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__malloc__)) ;

extern int  __VERIFIER_nondet_int(void);
extern _Bool __VERIFIER_nondet_bool(void);

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

_Atomic int* A;
_Atomic int min, max, N;
_Atomic _Bool v_assert, b1, b2;

_Atomic int *create_fresh_int_array(int size);

void* thread1(void* _argptr) {
  min = A[0];
  
  b1 = 1;
  
  for (int i=0; i<N; i++) {
    int read = A[i];
    if (min >= read) min = read;
  }

  return 0;
}

void* thread2(void* _argptr) {
  max = A[0];
  
  b2 = 1;
  
  for (int i=0; i<N; i++) {
    int read = A[i];
    if (max <= read) max = read;
  }

  return 0;
}

void* thread3(void* _argptr) {
  for (int i=0; i<N; i++) {
    assume_abort_if_not(A[i] > -2147483648);
    A[i]--;
  }

  return 0;
}

void* thread4(void* _argptr) {
  _Bool b1Local = b1;
  _Bool b2Local = b2;
  int maxLocal = max;
  v_assert = !b1Local || !b2Local || maxLocal >= 2147483647 || min <= maxLocal + 1;

  return 0;
}

int main() {
  pthread_t t1, t2, t3, t4;
  
  N = __VERIFIER_nondet_int();
  assume_abort_if_not(N > 0);
  A = create_fresh_int_array(N);
  
  // main method
  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_create(&t3, 0, thread3, 0);
  pthread_create(&t4, 0, thread4, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);
  pthread_join(t3, 0);
  pthread_join(t4, 0);
  
  assume_abort_if_not(!v_assert);
  reach_error();

  return 0;
}

_Atomic int *create_fresh_int_array(int size) {
  assume_abort_if_not(size >= 0);
  assume_abort_if_not(size <= (((size_t) 4294967295) / sizeof(_Atomic int)));

  _Atomic int* arr = (_Atomic int*)malloc(sizeof(_Atomic int) * (size_t)size);
  for (int i = 0; i < size; i++) {
    arr[i] = __VERIFIER_nondet_int();
  }
  return arr;
}
